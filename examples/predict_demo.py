"""Load the `champion` model from the MLflow registry and predict on held-out digits.

No training and no version number here: the alias decides which version is loaded.
The validation split is rebuilt from the params of the run that produced that version,
so the samples are images the model has not seen in training.

Only the MLflow client settings are needed: MLFLOW_TRACKING_URI,
MLFLOW_TRACKING_USERNAME, MLFLOW_TRACKING_PASSWORD (see ../.env.example).
Run from the repo root after examples/train_demo.py:

    uv run --env-file .env examples/predict_demo.py
"""

import mlflow
import numpy as np
from mlflow import MlflowClient
from sklearn import datasets
from sklearn.model_selection import train_test_split

MODEL_NAME = "demo-project-digits"
MODEL_ALIAS = "champion"
N_SAMPLES = 10
SAMPLE_SEED = 7

client = MlflowClient()
model_uri = f"models:/{MODEL_NAME}@{MODEL_ALIAS}"
model = mlflow.pyfunc.load_model(model_uri)

resolved = client.get_model_version_by_alias(MODEL_NAME, MODEL_ALIAS)
print(f"{model_uri} -> version {resolved.version}, run {resolved.run_id}\n")

train_params = client.get_run(resolved.run_id).data.params
digits = datasets.load_digits()
_, X_val, _, y_val = train_test_split(
    digits.data,
    digits.target,
    test_size=float(train_params["val_size"]),
    random_state=int(train_params["split_seed"]),
    stratify=digits.target,
)

rng = np.random.default_rng(SAMPLE_SEED)
idx = rng.choice(len(y_val), size=N_SAMPLES, replace=False)
predicted = model.predict(X_val[idx])
expected = y_val[idx]

print(f"{'val index':>9}  {'predicted':>9}  {'expected':>8}")
for i, p, t in zip(idx, predicted, expected, strict=True):
    mark = "" if p == t else "  MISS"
    print(f"{i:>9}  {p:>9}  {t:>8}{mark}")
print(f"\ncorrect: {int((predicted == expected).sum())} of {N_SAMPLES}")
