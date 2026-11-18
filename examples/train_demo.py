"""Train a small classifier on scikit-learn digits and track it in MLflow.

Logs params, git tags, input datasets, per-epoch metrics and a confusion matrix,
registers the model and moves the `champion` alias to the new version.

Only the MLflow client settings are needed: MLFLOW_TRACKING_URI,
MLFLOW_TRACKING_USERNAME, MLFLOW_TRACKING_PASSWORD (see ../.env.example).
AWS_* are not used: artifacts go through the MLflow server. Run from the repo root:

    uv run --env-file .env examples/train_demo.py
"""

import subprocess
from pathlib import Path

import matplotlib.pyplot as plt
import mlflow
import mlflow.sklearn
import numpy as np
from mlflow import MlflowClient
from mlflow.models import infer_signature
from sklearn import datasets
from sklearn.metrics import ConfusionMatrixDisplay, accuracy_score, log_loss
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier

EXPERIMENT_NAME = "demo-project/digits"
MODEL_NAME = "demo-project-digits"
MODEL_ALIAS = "champion"


def git_tags() -> dict[str, str]:
    repo_dir = Path(__file__).resolve().parent

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        return {
            "git_commit": git("rev-parse", "HEAD"),
            "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "git_dirty": str(bool(git("status", "--porcelain"))),
        }
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"git_commit": "unknown"}


mlflow.set_experiment(EXPERIMENT_NAME)


loaded_dataset = datasets.load_digits()
params = {
    "learning_rate_init": 0.001,
    "epochs": 10,
    "hidden_layer_sizes": (256, 256),
    "seed": 30405034,
    "val_size": 0.2,
    "split_seed": 42,
}

X_train, X_val, y_train, y_val = train_test_split(
    loaded_dataset.data,
    loaded_dataset.target,
    test_size=params["val_size"],
    random_state=params["split_seed"],
    stratify=loaded_dataset.target,
)

with mlflow.start_run():
    mlflow.log_params(params)
    mlflow.set_tags(git_tags())

    train_dataset = mlflow.data.from_numpy(
        X_train,
        targets=y_train,
        name="digits-train",
    )
    val_dataset = mlflow.data.from_numpy(
        X_val,
        targets=y_val,
        name="digits-val",
    )
    mlflow.log_inputs(
        datasets=[train_dataset, val_dataset],
        contexts=["training", "validation"],
        tags_list=[None, None],
    )

    model = MLPClassifier(
        hidden_layer_sizes=params["hidden_layer_sizes"],
        learning_rate_init=params["learning_rate_init"],
        random_state=params["seed"],
    )
    classes = np.unique(loaded_dataset.target)

    for epoch in range(params["epochs"]):
        model.partial_fit(X_train, y_train, classes=classes)

        val_proba = model.predict_proba(X_val)
        val_pred = model.classes_[val_proba.argmax(axis=1)]

        mlflow.log_metrics(
            {
                "train_loss": model.loss_,
                "val_loss": log_loss(y_val, val_proba, labels=model.classes_),
                "val_accuracy": accuracy_score(y_val, val_pred),
            },
            step=epoch,
        )

    val_pred = model.predict(X_val)
    fig, ax = plt.subplots(figsize=(7, 7))
    ConfusionMatrixDisplay.from_predictions(y_val, val_pred, ax=ax, colorbar=False)
    ax.set_title("Confusion matrix, val")
    mlflow.log_figure(fig, "confusion_matrix_val.png")
    plt.close(fig)

    signature = infer_signature(X_val, val_pred)
    model_info = mlflow.sklearn.log_model(
        model,
        name="model",
        signature=signature,
        input_example=X_val[:5],
        registered_model_name=MODEL_NAME,
        skops_trusted_types=[
            "sklearn.neural_network._stochastic_optimizers.AdamOptimizer"
        ],
    )
    version = model_info.registered_model_version

    MlflowClient().set_registered_model_alias(MODEL_NAME, MODEL_ALIAS, str(version))
    print(f"{MODEL_NAME} v{version} -> @{MODEL_ALIAS}")
