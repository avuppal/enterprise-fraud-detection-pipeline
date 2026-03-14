import json
import uuid
import time
import random
try:
    from faker import Faker
    fake = Faker()
except ImportError:
    fake = None

def generate_transaction():
    """Generates synthetic transaction data."""
    merchant = fake.company() if fake else f"MERCHANT_{random.randint(1,100)}"
    return {
        "tx_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "amount": round(random.uniform(5.0, 5000.0), 2),
        "merchant": merchant,
        "timestamp": time.time()
    }

def main():
    print("Starting transaction data simulation...")
    try:
        while True:
            tx = generate_transaction()
            print(f"Produced: {json.dumps(tx)}")
            # In a real scenario, publish to Kafka
            time.sleep(random.uniform(0.1, 1.0))
    except KeyboardInterrupt:
        print("Simulation stopped.")

if __name__ == "__main__":
    main()
