import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
import logging
from pydantic import BaseModel
from dotenv import load_dotenv
from urllib.parse import quote_plus
import asyncio
import json

# Load environment variables
load_dotenv(override=True)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Environment variables
AZURE_ENDPOINT = (
    "https://najd-ml-sweden-c-hskon.swedencentral.inference.ml.azure.com/score"
)
AZURE_API_KEY = os.getenv("AZURE_API_KEY")
print("azure key:", AZURE_API_KEY)

# MongoDB connection details
username = os.getenv("MONGO_USERNAME")
password = os.getenv("MONGO_PASSWORD")
host = os.getenv("MONGO_HOST")
database_name = os.getenv("MONGO_DATABASE", "chat_logs")

# Construct the full connection string with URL encoding
username_encoded = quote_plus(username)
password_encoded = quote_plus(password)
MONGO_URI = f"mongodb+srv://{username_encoded}:{password_encoded}@{host}/?tls=true&authMechanism=SCRAM-SHA-256&retrywrites=false&maxIdleTimeMS=120000"
print("mongo uri: ", MONGO_URI)
# MongoDB setup
client = AsyncIOMotorClient(MONGO_URI)
db = client[database_name]
collection = db.conversations

# Instructions
INSTRUCTIONS = 'أنتي  "نجد" المساعدة الذكية لخدمة الموظفين الخاصة بشركة "علم" من الذكاء الاصطناعي تم بناؤك للإجابة على الأسئلة حول الخدمات الداخلية لشركة علم، تعمل انظمة الموظفين في علم على اسهل، اسهل هي منصة (موقع) خاصة بشؤون خدمات الموظفين وطلباتهم واغلب الخدمات تعمل عن طريق اسهل. اجابتك تكون بناًء على المعلومات فقط اذا لم تجد الجواب في المعلومات اجب بأنك لم تجدها ضمن المعلومات .\nجميع معلوماتك هي من المعلومات المعطاة ولا تستطيع الاجابة على سؤال خارج هذه المعلومات '


# Pydantic models for request validation
class ChatRequest(BaseModel):
    question: str
    userId: str
    messageId: str
    userName: str
    conversationId: str
    questionTimestamp: datetime


class FeedbackRequest(BaseModel):
    question: str
    answer: str
    userId: str
    messageId: str
    conversationId: str
    feedback: str
    questionTimestamp: datetime
    answerTimestamp: datetime


@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    logger.info(f"Received chat request: {request}")
    try:
        response_generator = process_message(request.dict())
        return StreamingResponse(response_generator, media_type="text/plain")
    except Exception as e:
        logger.error(f"Error processing chat request: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/feedback")
async def feedback_endpoint(request: FeedbackRequest):
    logger.info(f"Received feedback: {request}")
    try:
        await save_feedback(request.dict())
        return {"message": "Feedback recorded successfully"}
    except Exception as e:
        logger.error(f"Error saving feedback: {e}")
        raise HTTPException(status_code=500, detail="Error saving feedback")


async def query_azure_endpoint(data: dict):
    headers = {
        "Authorization": f"Bearer {AZURE_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(AZURE_ENDPOINT, json=data, headers=headers)
        response.raise_for_status()
        return response.json()


async def process_message(data: dict):
    try:
        # Prepare the input for the LLM
        llm_input = {
            "input_data": {
                "input_string": [
                    {"role": "system", "content": INSTRUCTIONS},
                    {"role": "user", "content": data["question"]},
                ],
                "parameters": {"temperature": 0.7, "max_tokens": 2000},
            }
        }

        # Query LLM endpoint
        llm_response = await query_azure_endpoint(llm_input)

        # Extract the response from the LLM output
        full_response = llm_response.get("output", "")

        # Log the conversation
        log_entry = {
            "userId": data["userId"],
            "userName": data["userName"],
            "conversationId": data["conversationId"],
            "messageId": data["messageId"],
            "question": data["question"],
            "answer": full_response,
            "questionTimestamp": data["questionTimestamp"],
            "answerTimestamp": datetime.utcnow().isoformat(),
            "feedback": None,
            "logTimestamp": datetime.utcnow(),
        }
        print("saving the logs..")
        await collection.insert_one(log_entry)
        print("logs saved!")

        yield full_response

    except httpx.HTTPError as e:
        logger.error(f"HTTP error during Azure endpoint query: {e}")
        yield "I'm sorry, I'm having trouble accessing the necessary information right now. Please try again later."
    except Exception as e:
        logger.error(f"Error in process_message: {e}")
        yield "I apologize, but I'm experiencing technical difficulties. Please try again later."


async def save_feedback(data: dict):
    result = await collection.update_one(
        {"messageId": data["messageId"]},
        {
            "$set": {
                "feedback": data["feedback"],
                "questionTimestamp": data["questionTimestamp"],
                "answerTimestamp": data["answerTimestamp"],
            }
        },
    )

    if result.modified_count == 0:
        logger.warning(
            f"No document found for messageId: {data['messageId']}. Creating new entry."
        )
        await collection.insert_one(data)


async def test_connection():
    try:
        await client.server_info()
        print("Successfully connected to the database.")
        await collection.insert_one(
            {"test": "Connection successful from gateway server"}
        )
        print("Successfully inserted a test document.")
        return True
    except Exception as e:
        print(f"An error occurred while connecting to MongoDB: {e}")
        print(f"MongoDB URI: {MONGO_URI}")
        return False


# if __name__ == "__main__":
#     import uvicorn

#     load_dotenv()
#     print("Testing the MongoDB connection...")
#     connection_success = asyncio.run(test_connection())
#     print("MongoDB connection test completed.")

#     if connection_success:
#         print("Starting the FastAPI application...")
#         uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8100)))
#     else:
#         print("Failed to connect to MongoDB. The application will not start.")

if __name__ == "__main__":
    import uvicorn
    import asyncio

    async def main():
        print("Testing the MongoDB connection...")
        connection_success = await test_connection()
        print("MongoDB connection test completed.")

        if connection_success:
            print("Starting the FastAPI application...")
            config = uvicorn.Config(
                app, host="0.0.0.0", port=int(os.getenv("PORT", 8100))
            )
            server = uvicorn.Server(config)
            await server.serve()
        else:
            print("Failed to connect to MongoDB. The application will not start.")

    # Run the main function
    asyncio.run(main())
