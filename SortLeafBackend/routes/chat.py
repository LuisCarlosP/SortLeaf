import logging
import uuid
from fastapi import APIRouter, HTTPException, Depends, Request, Form, File, UploadFile
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, ValidationError as SchemaError
from core.rate_limit import limiter

from services.chat_service import ChatService, MaterialContext
from core.dependencies import get_chat_service
from core.dependencies import get_prediction_service, PredictionService
from core.file_validator import FileValidator, FileSizeExceededError
from routes.prediction import get_file_validator
from exceptions.validation_exceptions import ValidationError
from config.settings import (
    RATE_LIMIT_CHAT_SESSION,
    RATE_LIMIT_CHAT_MESSAGE,
    RATE_LIMIT_CHAT_HISTORY,
    RATE_LIMIT_CHAT_DELETE,
    RATE_LIMIT_CHAT_UPDATE
)
from config.settings import MAX_FILE_SIZE

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])


def get_turn_prediction_service(file: Optional[UploadFile] = File(None)):
    """Text-only conversations do not load the image model."""
    if file is None:
        return None
    try:
        return get_prediction_service()
    except Exception:
        logger.exception("Image model unavailable")
        return None


class ScannedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object: Literal["bottle", "jar", "can", "box", "cardboard", "newspaper", "carton", "container",
                    "cap", "label", "battery", "electronics", "bulb", "sanitary_waste", "food_scraps", "chemical_container"]
    material: Literal["plastic", "glass", "metal", "paper", "cardboard", "composite", "organic", "unknown"]
    route: Literal["likely_recyclable", "special_collection", "not_recyclable", "check_locally", "unknown"]


class LocalScan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["identified", "not_recyclable", "out_of_scope", "uncertain"]
    items: list[ScannedItem] = Field(max_length=8)


@router.post("/turn")
@limiter.limit(RATE_LIMIT_CHAT_MESSAGE)
async def send_conversation_turn(
    request: Request,
    message: str = Form("", max_length=1000),
    visual_context: Optional[str] = Form(None, max_length=2400),
    language: str = Form("es", pattern="^(es|en)$"),
    session_id: Optional[str] = Form(None, max_length=128),
    new_chat_id: Optional[uuid.UUID] = Form(None),
    request_id: Optional[uuid.UUID] = Form(None),
    scan_follow_up: bool = Form(False),
    observation_only: bool = Form(False),
    file: Optional[UploadFile] = File(None),
    chat_svc: ChatService = Depends(get_chat_service),
    prediction_service: Optional[PredictionService] = Depends(get_turn_prediction_service),
    file_validator: FileValidator = Depends(get_file_validator),
):
    """One conversation turn: text, an image, or both. Existing routes remain available."""
    message = message.strip()
    scan = None
    if visual_context is not None:
        if file is not None:
            raise HTTPException(status_code=422, detail="choose_one_image_source")
        try:
            scan = LocalScan.model_validate_json(visual_context)
        except SchemaError:
            raise HTTPException(status_code=422, detail="invalid_visual_context")
        if scan.outcome == "identified" and not scan.items:
            raise HTTPException(status_code=422, detail="invalid_visual_context")

    if observation_only and (not scan or scan.outcome == "identified"):
        raise HTTPException(422, "invalid_local_observation")

    if not message and file is None and scan is None:
        raise HTTPException(status_code=422, detail="A message or image is required")

    if session_id:
        try:
            await run_in_threadpool(chat_svc.require_session, session_id)
        except ValidationError:
            raise HTTPException(status_code=404, detail="session_expired")

    prediction = None
    material_context = None
    if file is not None:
        try:
            file_bytes = await file.read(MAX_FILE_SIZE + 1)
            file_validator.validate(file_bytes, file.content_type)
            prediction = await prediction_service.predict_image(file_bytes, file.filename, language)
            material_context = MaterialContext(
                prediction["class"], prediction["is_recyclable"], prediction["message"]
            )
        except FileSizeExceededError as exc:
            raise HTTPException(status_code=413, detail=str(exc))
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            logger.exception("Image classification failed during conversation")
            raise HTTPException(status_code=503, detail="image_analysis_unavailable")
        finally:
            await file.close()

    if session_id is None:
        session_id = str(new_chat_id or uuid.uuid4())
        await run_in_threadpool(chat_svc.create_chat_session, session_id, material_context, language)
        # Created with the current material.
        material_context = None

    prompt = message or (
        "¿Qué material es y cómo puedo separarlo?" if language == "es"
        else "What material is this and how can I sort it?"
    )
    if scan is not None:
        # Only allowlisted, bounded observations enter the user turn. The provider keeps it
        # in this conversation's history, including when the next question is text-only.
        prompt = f"Latest on-device photo observations: {scan.model_dump_json()}\n\nUser: {prompt}"
    activity = None
    if prediction:
        activity = {"materials": [prediction["class"]], "confidence": prediction["confidence"],
                    "recyclable": prediction["is_recyclable"]}
    elif scan and scan.items and not scan_follow_up:
        activity = {"materials": list(dict.fromkeys(item.material for item in scan.items))}
    local_answer = None
    if scan:
        material_context = MaterialContext(", ".join(dict.fromkeys(i.material for i in scan.items)) or "unknown",
                                           False, "Use the latest on-device observations and their sorting routes.")
        if observation_only:
            decisions = {
                "out_of_scope": ("No se distingue un residuo. Intenta con un objeto reciclable en primer plano.",
                                 "No waste item is visible. Try a close-up of a recyclable item."),
                "uncertain": ("Acerca el objeto y fotografía la etiqueta o el material con buena luz.",
                              "Move closer and photograph the label or material in good light."),
                "not_recyclable": ("Fuera del reciclaje ordinario. Intenta con un objeto reciclable.",
                                   "Outside ordinary recycling. Try a recyclable object."),
            }
            local_answer = decisions[scan.outcome][language == "en"]
    result = await run_in_threadpool(chat_svc.send_turn, session_id, prompt, message, material_context, activity,
                                     str(request_id) if request_id else None, local_answer)
    if result.get("error"):
        raise HTTPException(status_code=503, detail="assistant_unavailable")
    return {**result, "prediction": prediction}

# Request/response models
class CreateSessionRequest(BaseModel):
    """Request to create a chat session"""
    material_type: Optional[str] = Field(None, max_length=80, description="Identified material type")
    is_recyclable: Optional[bool] = Field(None, description="Whether the material is recyclable")
    material_info: Optional[str] = Field(None, max_length=1800, description="Additional material information")
    language: str = Field("en", description="Chat language (en/es)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "material_type": "plastic",
                "is_recyclable": True,
                "material_info": "PET plastic bottle",
                "language": "en"
            }
        }

class SendMessageRequest(BaseModel):
    """Request to send a message"""
    session_id: str = Field(..., description="Chat session ID")
    message: str = Field(..., min_length=1, max_length=1000, description="User message")
    
    class Config:
        json_schema_extra = {
            "example": {
                "session_id": "abc123-def456",
                "message": "How can I recycle this plastic bottle?"
            }
        }

class UpdateMaterialRequest(BaseModel):
    """Request to update material context"""
    session_id: str = Field(..., description="Chat session ID")
    material_type: str = Field(..., max_length=80, description="Identified material type")
    is_recyclable: bool = Field(..., description="Whether the material is recyclable")
    material_info: str = Field(..., max_length=1800, description="Additional material information")
    
    class Config:
        json_schema_extra = {
            "example": {
                "session_id": "abc123-def456",
                "material_type": "cardboard",
                "is_recyclable": True,
                "material_info": "Corrugated cardboard box"
            }
        }

@router.post("/session")
@limiter.limit(RATE_LIMIT_CHAT_SESSION)
def create_chat_session(
    request: Request,
    session_request: CreateSessionRequest,
    chat_svc: ChatService = Depends(get_chat_service)
):
    """
    Create a new chat session.
    
    If material information is provided, the chat is initialized with that context.
    """
    try:
        session_id = str(uuid.uuid4())
        
        material_context = None
        if session_request.material_type:
            material_context = MaterialContext(
                material_type=session_request.material_type,
                is_recyclable=session_request.is_recyclable or False,
                material_info=session_request.material_info or ""
            )
        
        result = chat_svc.create_chat_session(
            session_id=session_id,
            material_context=material_context,
            language=session_request.language
        )
        
        logger.info(f"Chat session created: {session_id}")
        return JSONResponse(content=result, status_code=201)
        
    except HTTPException:
        raise
    except ValidationError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        raise HTTPException(status_code=500, detail="Internal error creating chat session")

@router.post("/message")
@limiter.limit(RATE_LIMIT_CHAT_MESSAGE)
async def send_message(
    request: Request,
    message_request: SendMessageRequest,
    chat_svc: ChatService = Depends(get_chat_service)
):
    """
    Send a message to the chat and get a response.
    
    The chat will only respond to questions related to recycling and sustainability.
    """
    try:
        result = await run_in_threadpool(chat_svc.send_message,
            session_id=message_request.session_id,
            message=message_request.message
        )
        
        return JSONResponse(content=result)
        
    except ValidationError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        raise HTTPException(status_code=500, detail="Internal error processing message")

@router.get("/history/{session_id}")
@limiter.limit(RATE_LIMIT_CHAT_HISTORY)
def get_chat_history(
    request: Request,
    session_id: str,
    chat_svc: ChatService = Depends(get_chat_service)
):
    """
    Get the conversation history of a session.
    """
    try:
        history = chat_svc.get_chat_history(session_id)
        
        return JSONResponse(content={
            "session_id": session_id,
            "history": history
        })
        
    except ValidationError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting history: {e}")
        raise HTTPException(status_code=500, detail="Internal error getting history")

@router.delete("/session/{session_id}")
@limiter.limit(RATE_LIMIT_CHAT_DELETE)
def delete_chat_session(
    request: Request,
    session_id: str,
    chat_svc: ChatService = Depends(get_chat_service)
):
    """
    Delete a chat session.
    """
    try:
        deleted = chat_svc.delete_chat_session(session_id)
        
        if not deleted:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return JSONResponse(content={
            "message": "Session deleted successfully",
            "session_id": session_id
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        raise HTTPException(status_code=500, detail="Internal error deleting session")

@router.put("/material")
@limiter.limit(RATE_LIMIT_CHAT_UPDATE)
def update_material_context(
    request: Request,
    update_request: UpdateMaterialRequest,
    chat_svc: ChatService = Depends(get_chat_service)
):
    """
    Update the material context in an existing session.
    
    Useful when the user identifies a new material.
    """
    try:
        material_context = MaterialContext(
            material_type=update_request.material_type,
            is_recyclable=update_request.is_recyclable,
            material_info=update_request.material_info
        )
        
        result = chat_svc.update_material_context(
            session_id=update_request.session_id,
            material_context=material_context
        )
        
        return JSONResponse(content=result)
        
    except ValidationError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating context: {e}")
        raise HTTPException(status_code=500, detail="Internal error updating context")

@router.get("/conversations")
def list_conversations(chat_svc: ChatService = Depends(get_chat_service)):
    return chat_svc.list_chats()
