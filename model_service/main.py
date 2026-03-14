from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import onnxruntime as ort
import numpy as np
import uuid
from typing import Optional

app = FastAPI(title="Enterprise Fraud Detection Service", version="1.0.0")

class TransactionInput(BaseModel):
    tx_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Transaction ID")
    amount: float = Field(..., gt=0, description="Transaction amount")
    merchant: str = Field(..., description="Merchant name")
    user_id: str = Field(..., description="User ID")

class ScoreResponse(BaseModel):
    transaction_id: str
    anomaly_score: float
    is_fraud: bool
    processing_time_ms: float

# Placeholder for loading the optimized ONNX model
try:
    sess = ort.InferenceSession("model.onnx") 
except Exception as e:
    print(f"Model loading failed: {e}")
    sess = None

@app.get("/health")
def health_check():
    return {"status": "OK", "model_loaded": sess is not None}

@app.post("/score", response_model=ScoreResponse)
def score_transaction(data: TransactionInput):
    import time
    start_time = time.time()
    if not sess:
        # We simulate the model loading failure by returning random score for dev
        import random
        score = random.random()
    else:
        # Simulate inference
        dummy_input = np.random.rand(1, 10).astype(np.float32)
        scores = sess.run(None, {sess.get_inputs()[0].name: dummy_input})
        score = float(scores[0][0][0])
        
    proc_time = (time.time() - start_time) * 1000
    
    return ScoreResponse(
        transaction_id=data.tx_id,
        anomaly_score=score,
        is_fraud=score > 0.85,
        processing_time_ms=round(proc_time, 2)
    )
