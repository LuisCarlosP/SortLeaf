import pytest
from unittest.mock import MagicMock, patch
import numpy as np
from io import BytesIO
from PIL import Image
from services.trash_services import ImageProcessor, ResponseFormatter, ModelService
from exceptions import ImageProcessingError, ModelLoadError, PredictionError

def test_process_image_success():
    image = BytesIO()
    Image.new("RGB", (5, 5), (0, 128, 255)).save(image, format="PNG")
    result = ImageProcessor.process_image(image.getvalue())
    assert result.shape == (1, 224, 224, 3)
    assert result.dtype == np.float32
    np.testing.assert_allclose(result[0, 0, 0], [-1.0, 128 / 127.5 - 1, 1.0], atol=1e-6)

def test_process_image_error():
    with patch("PIL.Image.open", side_effect=Exception("Invalid image")):
        processor = ImageProcessor()
        with pytest.raises(ImageProcessingError):
            processor.process_image(b"fake_bytes")

def test_format_prediction_response():
    formatter = ResponseFormatter()
    response = formatter.format_prediction_response("plastic", 0.95)
    
    assert response["class"] == "plastic"
    assert response["confidence"] == 0.95
    assert response["is_recyclable"] is True

def test_format_prediction_response_unknown():
    formatter = ResponseFormatter()
    response = formatter.format_prediction_response("unknown_class", 0.5)
    
    assert response["class"] == "unknown_class"
    assert response["is_recyclable"] is False

def test_model_service_predict():
    with patch("tensorflow.keras.models.load_model") as mock_load:
        mock_model = MagicMock()
        predictions_array = np.array([[0.1, 0.9]])
        mock_model.predict.return_value = predictions_array
        mock_load.return_value = mock_model
        
        with patch("services.trash_services.CLASS_NAMES", ["paper", "plastic"]):
            service = ModelService()
            class_name, confidence = service.predict(np.zeros((1, 224, 224, 3)))
            assert class_name == "plastic"
            assert confidence == pytest.approx(0.9)


def test_six_class_score_is_not_softmaxed_again():
    service = ModelService()
    service._model_loaded = True
    service.model = MagicMock()
    service.model.predict.return_value = np.array([[0.01, 0.01, 0.01, 0.01, 0.95, 0.01]])
    label, score = service.predict(np.zeros((1, 224, 224, 3)))
    assert label == "plastic"
    assert score == pytest.approx(0.95)


@pytest.mark.parametrize("output", [
    [[float("nan"), 0, 0, 0, 0, 1]],
    [[-1, 0, 0, 0, 1, 1]],
    [[0.1, 0.1, 0.1, 0.1, 0.1, 0.1]],
    [[1, 2, 3, 4, 5, 6]],
    [[0.1, 0.9]],
    [[0, 0, 0, 0, 1, 0], [0, 0, 0, 0, 1, 0]],
])
def test_invalid_probabilities_fail_instead_of_displaying_confidence(output):
    service = ModelService()
    service._model_loaded = True
    service.model = MagicMock()
    service.model.predict.return_value = np.array(output)
    with pytest.raises(PredictionError):
        service.predict(np.zeros((1, 224, 224, 3)))
