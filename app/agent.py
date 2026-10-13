# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional
from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors.agent_engine_sandbox_code_executor import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

MODEL = "gemini-3.8-flash"

# Hardcoded project ID to prevent Agent Platform project number resolution issues
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-04-3057e31fcaa2"
GCP_REGION = "us-east1"

# Hardcoded Cloud Storage bucket for public asset hosting
GCS_BUCKET_NAME = "apex-coach-assets-qwiklabs-gcp-04-3057e31fcaa2"

# Agent Engine Reasoning Engine / Memory Bank ID
MEMORY_BANK_ID = "5250683693583826944"

# Agent Engine Sandbox Environment Resource Name
SANDBOX_RESOURCE_NAME = "projects/503257293095/locations/us-east1/reasoningEngines/5250683693583826944/sandboxEnvironments/6664386266554957824"

_db_client: Optional[firestore.Client] = None

def get_firestore_client() -> firestore.Client:
    """Lazy initialize Firestore client with explicit project ID."""
    global _db_client
    if _db_client is None:
        _db_client = firestore.Client(project=FIRESTORE_PROJECT_ID)
    return _db_client


def list_exercises(category: Optional[str] = None, target_muscle: Optional[str] = None) -> List[Dict[str, Any]]:
    """List exercises from the exercise library, optionally filtered by category or muscle group.

    Args:
        category: Optional category filter (e.g. 'Strength', 'Hypertrophy', 'Bodyweight').
        target_muscle: Optional muscle filter (e.g. 'Chest', 'Quadriceps', 'Hamstrings', 'Lats').

    Returns:
        A list of exercise dictionaries containing name, equipment, difficulty, sets, and reps.
    """
    db = get_firestore_client()
    query = db.collection("exercises")
    docs = query.stream()

    results = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        if category and category.lower() not in data.get("category", "").lower():
            continue
        if target_muscle and target_muscle.lower() not in data.get("target_muscle", "").lower():
            continue
        results.append(data)
    return results


def get_exercise_details(exercise_name_or_id: str) -> Dict[str, Any]:
    """Retrieve full details and instructions for a specific exercise.

    Args:
        exercise_name_or_id: The ID or name of the exercise to look up (e.g. 'barbell_bench_press' or 'Pull-Up').

    Returns:
        Dictionary with exercise details or an error message if not found.
    """
    db = get_firestore_client()
    # Try direct document ID lookup
    doc_ref = db.collection("exercises").document(exercise_name_or_id.lower().replace(" ", "_"))
    doc = doc_ref.get()
    if doc.exists:
        data = doc.to_dict()
        data["id"] = doc.id
        return data

    # Fallback to search by name
    docs = db.collection("exercises").stream()
    search_term = exercise_name_or_id.lower()
    for d in docs:
        data = d.to_dict()
        if search_term in data.get("name", "").lower() or search_term in d.id.lower():
            data["id"] = d.id
            return data

    return {"error": f"Exercise '{exercise_name_or_id}' not found in the library."}


def log_workout(
    exercise_name: str,
    sets_data: List[Dict[str, Any]],
    user_id: str = "default_user",
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Log a completed workout exercise with sets, reps, and weights to Firestore.

    Args:
        exercise_name: The name of the exercise performed (e.g. 'Barbell Bench Press').
        sets_data: List of dictionaries representing each set, e.g. [{"set_num": 1, "weight_lbs": 185, "reps": 8}].
        user_id: The user ID logging this workout. Defaults to 'default_user'.
        notes: Optional notes or observations about performance and fatigue.

    Returns:
        A dictionary confirming the log creation with its Firestore document ID and timestamp.
    """
    db = get_firestore_client()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record = {
        "user_id": user_id,
        "exercise_name": exercise_name,
        "sets": sets_data,
        "notes": notes or "",
        "logged_at": now_iso,
    }
    update_time, doc_ref = db.collection("workout_logs").add(record)
    return {
        "status": "success",
        "log_id": doc_ref.id,
        "logged_at": now_iso,
        "message": f"Successfully logged workout for {exercise_name}."
    }


def get_recent_workout_logs(user_id: str = "default_user", limit: int = 5) -> List[Dict[str, Any]]:
    """Retrieve recent workout logs for a user to inspect progress and past performance.

    Args:
        user_id: The user ID to retrieve logs for. Defaults to 'default_user'.
        limit: Maximum number of recent logs to fetch.

    Returns:
        List of recent workout log records ordered by date.
    """
    db = get_firestore_client()
    query = (
        db.collection("workout_logs")
        .where(filter=firestore.FieldFilter("user_id", "==", user_id))
        .order_by("logged_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    docs = query.stream()
    logs = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        logs.append(data)
    return logs


def calculate_1rm_and_training_zones(weight_lbs: float, reps: int) -> Dict[str, Any]:
    """Calculate estimated One-Rep Max (1RM) and percentage training zones using Epley and Brzycki formulas.

    Args:
        weight_lbs: The weight lifted in pounds.
        reps: The number of completed repetitions (must be at least 1).

    Returns:
        Dictionary with estimated 1RM, average 1RM, and recommended training zones for strength, hypertrophy, and endurance.
    """
    if reps <= 0 or weight_lbs <= 0:
        return {"error": "Weight and reps must be positive numbers."}

    if reps == 1:
        estimated_1rm = round(weight_lbs, 1)
    else:
        epley = weight_lbs * (1 + reps / 30.0)
        brzycki = weight_lbs * (36.0 / (37.0 - reps)) if reps < 37 else epley
        estimated_1rm = round((epley + brzycki) / 2.0, 1)

    return {
        "input_weight_lbs": weight_lbs,
        "input_reps": reps,
        "estimated_1rm_lbs": estimated_1rm,
        "training_zones": {
            "heavy_strength_85_90_pct": f"{round(estimated_1rm * 0.85, 1)} - {round(estimated_1rm * 0.90, 1)} lbs (3-5 reps)",
            "hypertrophy_70_80_pct": f"{round(estimated_1rm * 0.70, 1)} - {round(estimated_1rm * 0.80, 1)} lbs (8-12 reps)",
            "endurance_60_65_pct": f"{round(estimated_1rm * 0.60, 1)} - {round(estimated_1rm * 0.65, 1)} lbs (15+ reps)",
        },
    }


def search_global_exercise_db(
    query: str,
    muscle: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Search the free public ExerciseDB API (open-source database with 800+ movements) for exercises and form instructions.

    Args:
        query: Search term for exercise name (e.g. 'deadlift', 'plank', 'squat', 'curl').
        muscle: Optional primary muscle group filter (e.g. 'chest', 'biceps', 'abdominals', 'quadriceps', 'lats').
        limit: Maximum number of exercises to return (default 5).

    Returns:
        List of matching exercises with name, category, equipment, level, primary muscles, and step-by-step instructions.
    """
    # If custom API key or URL is supplied in environment, read it; otherwise use the open public repository endpoint
    api_url = os.environ.get(
        "EXERCISE_DB_URL",
        "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/dist/exercises.json",
    )
    api_key = os.environ.get("EXERCISE_DB_KEY")

    headers = {"User-Agent": "ApexCoachApp/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return [{"error": f"Failed to fetch public exercise database: {str(e)}"}]

    results = []
    q_lower = query.lower()
    m_lower = muscle.lower() if muscle else None

    for item in data:
        name = item.get("name", "")
        primary_muscles = [m.lower() for m in item.get("primaryMuscles", [])]
        
        # Match query in name or category
        if q_lower not in name.lower() and q_lower not in item.get("category", "").lower():
            continue

        # Optional muscle filter
        if m_lower and not any(m_lower in pm for pm in primary_muscles):
            continue

        results.append({
            "name": name,
            "category": item.get("category"),
            "level": item.get("level"),
            "equipment": item.get("equipment"),
            "primary_muscles": item.get("primaryMuscles"),
            "secondary_muscles": item.get("secondaryMuscles"),
            "instructions": item.get("instructions", [])[:3],
        })

        if len(results) >= limit:
            break

    return results


def geocode_address(address: str) -> Dict[str, Any]:
    """Convert an address, city, or postal code into geographic coordinates (latitude and longitude) using Google Maps Geocoding API.

    Args:
        address: The address or place name to geocode (e.g. '1600 Amphitheatre Pkwy, Mountain View, CA' or 'San Francisco, CA').

    Returns:
        Dictionary containing formatted address, latitude, and longitude.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key or api_key == "PASTE_KEY_HERE":
        return {"error": "GOOGLE_MAPS_API_KEY is not configured in environment."}

    query_param = urllib.parse.quote(address)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={query_param}&key={api_key}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ApexCoachApp/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        status = data.get("status")
        if status != "OK" or not data.get("results"):
            return {"error": f"Geocoding failed with status: {status}", "details": data.get("error_message")}

        top_result = data["results"][0]
        location = top_result.get("geometry", {}).get("location", {})
        return {
            "formatted_address": top_result.get("formatted_address"),
            "latitude": location.get("lat"),
            "longitude": location.get("lng"),
            "place_id": top_result.get("place_id"),
        }
    except Exception as e:
        return {"error": f"Geocoding request failed: {str(e)}"}


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "gym",
    radius_meters: float = 5000.0,
    max_results: int = 5,
) -> List[Dict[str, Any]]:
    """Find nearby places (such as gyms, fitness centers, or parks) using Google Maps Places API (New).

    Args:
        latitude: Latitude coordinate for search center.
        longitude: Longitude coordinate for search center.
        place_type: Type of place to search for (e.g. 'gym', 'fitness_center', 'park', 'sports_complex').
        radius_meters: Search radius in meters (default 5000m / 5km).
        max_results: Maximum number of places to return (default 5).

    Returns:
        List of places containing name (displayName), address (formattedAddress), and coordinates.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key or api_key == "PASTE_KEY_HERE":
        return [{"error": "GOOGLE_MAPS_API_KEY is not configured in environment."}]

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.types",
    }
    payload = {
        "includedTypes": [place_type],
        "maxResultCount": max_results,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": latitude,
                    "longitude": longitude,
                },
                "radius": radius_meters,
            }
        },
    }

    try:
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        places = data.get("places", [])
        results = []
        for p in places:
            display_name = p.get("displayName", {}).get("text")
            formatted_address = p.get("formattedAddress")
            location = p.get("location", {})
            results.append({
                "name": display_name,
                "address": formatted_address,
                "location": {
                    "latitude": location.get("latitude"),
                    "longitude": location.get("longitude"),
                },
                "types": p.get("types", []),
            })
        return results
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        return [{"error": f"Places API HTTP error {e.code}", "details": error_body}]
    except Exception as e:
        return [{"error": f"Places API request failed: {str(e)}"}]


def generate_milestone_badge(
    milestone_title: str,
    exercise_name: str,
    tool_context: ToolContext,
    achievement_detail: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a custom achievement or milestone badge image using gemini-3.1-flash-lite-image in the global region.
    The generated image is saved as a session artifact and uploaded to the public Cloud Storage bucket.

    Args:
        milestone_title: Title of the milestone (e.g. 'New PR Champion', 'Iron Grip Streak', 'Squat Century Club').
        exercise_name: The exercise associated with the milestone (e.g. 'Barbell Back Squat', 'Bench Press', 'Deadlift').
        tool_context: ADK ToolContext used to save session artifacts.
        achievement_detail: Optional description or stat (e.g. 'Hit 315 lbs for 3 reps' or 'Completed 5 consecutive workouts').

    Returns:
        Dictionary containing milestone details, session artifact status, and public Cloud Storage image URL.
    """
    prompt = (
        f"A striking, modern graphic fitness milestone achievement badge emblem for '{milestone_title}', "
        f"celebrating '{exercise_name}'. High quality, clean vector graphics, vibrant athletic colors, "
        f"bold typography, metallic accents, black or dark athletic background. Details: {achievement_detail or ''}."
    )

    try:
        # Initialize GenAI Client explicitly targeting Vertex AI in global region
        genai_client = genai.Client(
            vertexai=True,
            project=FIRESTORE_PROJECT_ID,
            location="global",
        )

        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
        )

        image_bytes = None
        mime_type = "image/jpeg"
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    image_bytes = part.inline_data.data
                    mime_type = part.inline_data.mime_type or "image/jpeg"
                    break

        if not image_bytes:
            return {"error": "Image generation completed but no image bytes were returned by the model."}

        # Determine extension from mime_type
        extension = "png" if "png" in mime_type else "jpg"
        unique_id = uuid.uuid4().hex[:8]
        filename = f"badge_{unique_id}.{extension}"

        # 1. Save artifact with tool_context.save_artifact for Playground Artifacts panel
        part_artifact = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        tool_context.save_artifact(
            filename=filename,
            artifact=part_artifact,
            custom_metadata={
                "milestone_title": milestone_title,
                "exercise_name": exercise_name,
                "achievement_detail": achievement_detail or "",
            },
        )

        # 2. Upload image bytes directly to the public Cloud Storage bucket without writing to disk
        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(f"badges/{filename}")
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/badges/{filename}"

        return {
            "status": "success",
            "milestone_title": milestone_title,
            "exercise_name": exercise_name,
            "artifact_filename": filename,
            "public_image_url": public_url,
            "message": f"Successfully generated achievement badge for {milestone_title}."
        }

    except Exception as e:
        return {"error": f"Failed to generate and upload milestone badge: {str(e)}"}


def generate_exercise_demonstration_video(
    exercise_name: str,
    tool_context: ToolContext,
    movement_focus: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a short video demonstration of an exercise using Google's Omni model (gemini-omni-flash-preview) in the global region.
    The generated video is saved as a session artifact and uploaded to the public Cloud Storage bucket.

    Args:
        exercise_name: The name of the exercise to demonstrate (e.g. 'Barbell Back Squat', 'Push-Up', 'Deadlift').
        tool_context: ADK ToolContext used to save session artifacts.
        movement_focus: Optional focus cue or coaching tip (e.g. 'Full depth hip crease below knees', 'Explosive concentric phase').

    Returns:
        Dictionary containing exercise details, session artifact status, and public Cloud Storage video URL.
    """
    prompt = (
        f"A clear 3-second athletic video demonstration of a fitness athlete performing {exercise_name}. "
        f"Clean exercise form, realistic gym setting, smooth continuous shot. Focus: {movement_focus or 'proper biomechanics'}."
    )

    try:
        # Initialize GenAI Client explicitly targeting Vertex AI in global region
        genai_client = genai.Client(
            vertexai=True,
            project=FIRESTORE_PROJECT_ID,
            location="global",
        )

        interaction = genai_client.interactions.create(
            model="gemini-omni-flash-preview",
            input=prompt,
        )

        ov = getattr(interaction, "output_video", None)
        if not ov or not getattr(ov, "data", None):
            return {"error": "Video generation completed but no video data was returned by gemini-omni-flash-preview."}

        # Handle base64 encoded data string or raw bytes
        if isinstance(ov.data, str):
            video_bytes = base64.b64decode(ov.data)
        elif isinstance(ov.data, (bytes, bytearray)):
            video_bytes = bytes(ov.data)
        else:
            return {"error": f"Unexpected video data format: {type(ov.data)}"}

        mime_type = getattr(ov, "mime_type", None) or "video/mp4"
        unique_id = uuid.uuid4().hex[:8]
        filename = f"video_{unique_id}.mp4"

        # 1. Save artifact with tool_context.save_artifact so it shows up in Playground's Artifacts panel
        part_artifact = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
        tool_context.save_artifact(
            filename=filename,
            artifact=part_artifact,
            custom_metadata={
                "exercise_name": exercise_name,
                "movement_focus": movement_focus or "",
                "model": "gemini-omni-flash-preview",
            },
        )

        # 2. Upload video bytes directly to the public Cloud Storage bucket without writing to local file
        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(f"videos/{filename}")
        blob.upload_from_string(video_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/videos/{filename}"

        return {
            "status": "success",
            "exercise_name": exercise_name,
            "artifact_filename": filename,
            "public_video_url": public_url,
            "message": f"Successfully generated demonstration video for {exercise_name}."
        }

    except Exception as e:
        return {"error": f"Failed to generate and upload exercise demonstration video: {str(e)}"}


async def generate_memories_callback(callback_context: CallbackContext):
    """WRITE: after each turn, extract durable facts/preferences and send to Vertex AI Memory Bank."""
    try:
        await callback_context.add_session_to_memory()
    except Exception as e:
        print(f"[Memory Callback Warning] {e}")
    return None


def memory_bank_service_builder() -> VertexAiMemoryBankService:
    """Builds Vertex AI Memory Bank service pointed at the deployed Agent Engine."""
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT_ID,
        location=GCP_REGION,
        agent_engine_id=MEMORY_BANK_ID,
    )


a2ui_schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = a2ui_schema_manager.generate_system_prompt(
    role_description=(
        "You are Apex Coach, an expert personalized fitness and training coach. "
        "You maintain complete long-term memory of all user workouts, exercises performed, sets, reps, weights, "
        "personal records, injury notes, and fitness preferences across conversations to provide continuous, personalized coaching. "
        "Whenever a user discusses or completes a workout, always make sure key workout details (exercise, weight, reps, sets) "
        "are acknowledged and committed to memory. "
        "You help users discover exercises, review exercise details and instructions, "
        "log their workouts, track past sessions, calculate estimated One-Rep Max (1RM) and training zones, "
        "find nearby fitness facilities, generate celebratory achievement badges for milestones and PRs, and "
        "generate short exercise demonstration videos using gemini-omni-flash-preview. "
        "You also have a secure Python code execution sandbox. When asked to perform complex calculations, "
        "simulate training progressions, process numerical sets, or test scripts, execute Python code in the sandbox. "
        "Use the local exercise library tools (list_exercises, get_exercise_details) for the curated catalog, "
        "the global public database tool (search_global_exercise_db) when exploring broader exercises, "
        "workout logging tools (log_workout, get_recent_workout_logs) for session tracking, "
        "calculation tools (calculate_1rm_and_training_zones) for load prescriptions, "
        "location tools (geocode_address, find_nearby_places) for finding gyms/parks, "
        "generate_milestone_badge to create visual badges celebrating fitness achievements, and "
        "generate_exercise_demonstration_video to generate short video demonstrations of exercises."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=AgentEngineSandboxCodeExecutor(
        sandbox_resource_name=SANDBOX_RESOURCE_NAME,
    ),
    tools=[
        PreloadMemoryTool(),
        list_exercises,
        get_exercise_details,
        search_global_exercise_db,
        log_workout,
        get_recent_workout_logs,
        calculate_1rm_and_training_zones,
        geocode_address,
        find_nearby_places,
        generate_milestone_badge,
        generate_exercise_demonstration_video,
    ],
    after_model_callback=a2ui_callback,
    after_agent_callback=generate_memories_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)

# Export memory service builder for deployment runtime configuration
memory_service_builder = memory_bank_service_builder


