"""Export the existing Keras model for the CPU-only Render container."""
from pathlib import Path

import numpy as np
import tensorflow as tf
from tensorflow.python.framework.convert_to_constants import convert_variables_to_constants_v2


def main() -> None:
    models = Path(__file__).resolve().parents[1] / "models"
    model = tf.keras.models.load_model(models / "SortLeafV2.h5", compile=False)
    shape = (1, *model.input_shape[1:])
    infer = tf.function(lambda image: model(image, training=False))
    frozen = convert_variables_to_constants_v2(infer.get_concrete_function(tf.TensorSpec(shape, tf.float32)))
    data = tf.lite.TFLiteConverter.from_concrete_functions([frozen]).convert()
    runtime = tf.lite.Interpreter(model_content=data, num_threads=1)
    runtime.allocate_tensors()
    rng = np.random.default_rng(42)
    for image in [np.zeros(shape, np.float32), rng.uniform(-1, 1, shape).astype(np.float32)]:
        runtime.set_tensor(runtime.get_input_details()[0]["index"], image)
        runtime.invoke()
        scores = runtime.get_tensor(runtime.get_output_details()[0]["index"])
        np.testing.assert_allclose(scores, infer(image).numpy(), rtol=1e-4, atol=1e-5)
    (models / "SortLeafV2.tflite").write_bytes(data)


if __name__ == "__main__":
    main()
