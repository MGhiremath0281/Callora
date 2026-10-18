from fastapi import FastAPI

app = FastAPI(title="Callora Backend")

@app.get("/")
def read_root():
    return {"message": "Welcome to Callora FastAPI Backend"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}
