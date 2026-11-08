
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)

response = client.models.generate_content(
    model="gemini-3.1-flash-lite",
    contents=(
        "Classify this event: "
        "'Coldplay Experience Tribute Concert'. "
        "Is this a real Coldplay performance "
        "or a tribute show? Answer in one sentence."
    )
)

print(response.text)
