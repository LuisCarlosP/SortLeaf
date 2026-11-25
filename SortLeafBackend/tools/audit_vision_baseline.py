"""Inspect the stored model without loading TensorFlow or reporting application secrets."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

import h5py


def audit(model_path: Path) -> dict:
    settings = Path(__file__).resolve().parents[1] / "config" / "settings.py"
    declarations = ast.parse(settings.read_text(encoding="utf-8"))
    class_order = next(
        ast.literal_eval(node.value)
        for node in declarations.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "CLASS_NAMES" for target in node.targets)
    )
    with model_path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    with h5py.File(model_path, "r") as model:
        config = json.loads(model.attrs["model_config"])
        layers = config["config"]["layers"]
        final = layers[-1]
        input_layers = [layer["config"] for layer in layers if layer["class_name"] == "InputLayer"]
    return {
        "model_file": model_path.name,
        "sha256": digest,
        "last_layer": final["class_name"],
        "output_units": final["config"].get("units"),
        "output_activation": final["config"].get("activation"),
        "input_layers": input_layers,
        "runtime_class_order": class_order,
        "runtime_preprocessing": "PIL RGB, resize 224x224, MobileNetV2 preprocess_input",
        "training_class_order_verified": False,
        "training_preprocessing_verified": False,
        "calibration_measured": False,
        "note": "Training metadata and labeled evaluation photos are required to verify class order, preprocessing parity and accuracy. Softmax output is a model score, not calibrated accuracy.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=Path(__file__).resolve().parents[1] / "models" / "SortLeafV2.h5")
    args = parser.parse_args()
    print(json.dumps(audit(args.model), indent=2))
