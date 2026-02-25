from fastapi import FastAPI
from pydantic import BaseModel
import time

# --- Configuration ---
APP_TITLE = "Mock Fraud Prediction Service"
APP_DESCRIPTION = "A placeholder API for the Fraud Detection Pipeline, simulating a model prediction endpoint."
APP_VERSION = "0.1.0-mock"
PORT = 8001
HOST = "0.0.0.0"

app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
)

# --- Data Models ---

class TransactionFeatures(BaseModel):
    """Schema for the expected input payload."""
    user_id: str
    amount: float
    merchant_id: str
    timestamp: float = time.time()
    feature_vector_len: int = 10 # Mock feature count

class PredictionResponse(BaseModel):
    """Schema for the prediction output."""
    request_id: str
    fraud_score: float
    prediction_time_ms: int
    model_version: str = APP_VERSION

# --- Mock Logic ---

def mock_fraud_prediction(features: TransactionFeatures) -> float:
    """
    Simulates the core model scoring logic.
    In a real scenario, this would load and run the ML model.
    Mock logic: return a higher score for large transactions.
    """
    if features.amount > 500.0:
        return 0.95  # High risk
    elif features.amount > 100.0:
        return 0.45  # Medium risk
    else:
        return 0.10  # Low risk

# --- Endpoints ---

@app.get("/health", response_model=dict)
async def health_check():
    """Simple health check endpoint."""
    return {"status": "ok", "service": "prediction_api", "version": APP_VERSION}

@app.post("/predict", response_model=PredictionResponse)
async def predict_fraud(features: TransactionFeatures):
    """Accepts transaction data and returns a mock fraud score."""
    start_time = time.time()
    
    # Generate a request ID for tracking
    request_id = f"req-{int(start_time * 1000)}-{hash(features.user_id) & 0xFFFF:04x}"
    
    # Perform mock scoring
    score = mock_fraud_prediction(features)
    
    end_time = time.time()
    latency_ms = int((end_time - start_time) * 1000)
    
    return PredictionResponse(
        request_id=request_id,
        fraud_score=score,
        prediction_time_ms=latency_ms
    )

# --- Execution ---
# Note: To run this, you would typically use: uvicorn prediction_api:app --host 0.0.0.0 --port 8001
# For this simulation, we just write the file.