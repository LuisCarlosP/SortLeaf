import numpy as np
from PIL import Image
from io import BytesIO
from typing import Tuple, Dict, Any
from pathlib import Path
from threading import Lock
import logging

from config.settings import IMAGE_WIDTH, IMAGE_HEIGHT, CLASS_NAMES, get_recyclable_info, MODEL_PATH
from exceptions import ModelLoadError, PredictionError, ImageProcessingError

logger = logging.getLogger(__name__)

class LiteClassifier:
    def __init__(self, path: str):
        from tflite_runtime.interpreter import Interpreter

        self.runtime = Interpreter(model_path=path, num_threads=1)
        self.runtime.allocate_tensors()
        self.input_index = self.runtime.get_input_details()[0]["index"]
        self.output_index = self.runtime.get_output_details()[0]["index"]
        self.lock = Lock()

    def predict(self, image: np.ndarray) -> np.ndarray:
        with self.lock:
            self.runtime.set_tensor(self.input_index, image)
            self.runtime.invoke()
            return self.runtime.get_tensor(self.output_index)

class ModelService:
    
    def __init__(self):
        self.model = None
        self._model_loaded = False
    
    def load(self) -> None:
        """Lazy load the model on first use."""
        if not self._model_loaded:
            self._load_model()
            self._model_loaded = True
    
    def _load_model(self) -> None:
        try:
            if Path(MODEL_PATH).suffix == ".tflite":
                self.model = LiteClassifier(MODEL_PATH)
            else:
                from tensorflow.keras.models import load_model
                self.model = load_model(MODEL_PATH, compile=False)
            logger.info(f"Model loaded successfully from {MODEL_PATH}")
        except Exception as e:
            logger.error(f"Error loading model: {e}")
            raise ModelLoadError(MODEL_PATH, str(e))
    
    def predict(self, img_array: np.ndarray) -> Tuple[str, float]:
        self.load()
        try:
            predictions = np.asarray(self.model.predict(img_array))
            # SortLeafV2 already has a six-class softmax output. Applying it
            # again compresses a confident prediction towards a uniform score.
            if predictions.shape != (1, len(CLASS_NAMES)):
                raise ValueError("Unexpected classification output shape")
            probabilities = predictions[0]
            if (not np.all(np.isfinite(probabilities)) or
                    np.any(probabilities < 0) or np.any(probabilities > 1) or
                    not np.isclose(probabilities.sum(), 1.0, atol=1e-4)):
                raise ValueError("Classifier must return normalized probabilities")
            index = int(np.argmax(probabilities))
            class_name = CLASS_NAMES[index]
            confidence = float(probabilities[index])
            
            logger.info(f"Prediction made: {class_name} with confidence {confidence:.2f}")
            return class_name, confidence
            
        except Exception as e:
            logger.error(f"Error in prediction: {e}")
            raise PredictionError("Error performing prediction", str(e))

class ImageProcessor:
    
    @staticmethod
    def process_image(file_bytes: bytes) -> np.ndarray:
        try:
            image = Image.open(BytesIO(file_bytes)).convert('RGB')
            image = image.resize((IMAGE_WIDTH, IMAGE_HEIGHT))
            # MobileNetV2 expects RGB pixels scaled from [0, 255] to [-1, 1].
            img_array = (np.asarray(image, dtype=np.float32) / 127.5 - 1.0)[None, ...]
            
            logger.info("Image processed successfully")
            return img_array
            
        except Exception as e:
            logger.error(f"Error processing image: {e}")
            raise ImageProcessingError("Error processing image", str(e))

class ResponseFormatter:
    
    @staticmethod
    def format_prediction_response(class_name: str, confidence: float, language: str = "en") -> Dict[str, Any]:
        try:
            is_recyclable, message = get_recyclable_info(class_name, language)
            
            return {
                "class": class_name,
                "confidence": confidence,
                "is_recyclable": is_recyclable,
                "message": message
            }
            
        except Exception as e:
            logger.error(f"Error formatting response: {e}")
            raise RuntimeError(f"Error formatting response: {e}")
    
    @staticmethod
    def format_error_response(error_message: str, status_code: int = 500) -> Dict[str, Any]:
        return {
            "error": True,
            "message": error_message,
            "code": status_code
        }
