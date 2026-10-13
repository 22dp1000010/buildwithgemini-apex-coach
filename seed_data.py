import datetime
from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-04-3057e31fcaa2"

def seed():
    db = firestore.Client(project=PROJECT_ID)
    print(f"Connecting to Firestore in project: {PROJECT_ID}")

    exercises = [
        {
            "id": "barbell_bench_press",
            "name": "Barbell Bench Press",
            "category": "Strength",
            "target_muscle": "Chest",
            "secondary_muscles": ["Triceps", "Anterior Deltoids"],
            "equipment": "Barbell, Flat Bench",
            "difficulty": "Intermediate",
            "instructions": "Lie flat on the bench, grip the barbell slightly wider than shoulder-width, lower the bar smoothly to your mid-chest, and press explosively upwards until lockout.",
            "default_sets": 4,
            "default_reps": "8-10"
        },
        {
            "id": "barbell_back_squat",
            "name": "Barbell Back Squat",
            "category": "Strength",
            "target_muscle": "Quadriceps",
            "secondary_muscles": ["Glutes", "Hamstrings", "Core"],
            "equipment": "Barbell, Squat Rack",
            "difficulty": "Intermediate",
            "instructions": "Rest barbell on upper traps, keep chest tall and feet shoulder-width apart. Squat down until thighs are parallel or below parallel to the floor, driving knees outward and pressing through midfoot.",
            "default_sets": 4,
            "default_reps": "6-8"
        },
        {
            "id": "conventional_deadlift",
            "name": "Conventional Deadlift",
            "category": "Strength",
            "target_muscle": "Hamstrings & Lower Back",
            "secondary_muscles": ["Glutes", "Lats", "Traps", "Forearms"],
            "equipment": "Barbell, Weight Plates",
            "difficulty": "Advanced",
            "instructions": "Stand with feet hip-width apart with the bar over mid-foot. Hinge at hips, grip the bar just outside legs, brace core, and drive through the floor to stand upright. Lower under control.",
            "default_sets": 3,
            "default_reps": "5"
        },
        {
            "id": "pull_up",
            "name": "Pull-Up",
            "category": "Bodyweight",
            "target_muscle": "Lats",
            "secondary_muscles": ["Biceps", "Upper Back", "Core"],
            "equipment": "Pull-Up Bar",
            "difficulty": "Intermediate",
            "instructions": "Hang from pull-up bar with an overhand grip wider than shoulders. Pull chest up towards the bar by driving elbows down, pausing briefly at the top before lowering with control.",
            "default_sets": 3,
            "default_reps": "8-12"
        },
        {
            "id": "dumbbell_shoulder_press",
            "name": "Dumbbell Overhead Shoulder Press",
            "category": "Strength",
            "target_muscle": "Shoulders (Deltoids)",
            "secondary_muscles": ["Triceps", "Upper Chest"],
            "equipment": "Dumbbells, Bench (optional)",
            "difficulty": "Beginner",
            "instructions": "Hold dumbbells at shoulder height with palms facing forward. Press dumbbells overhead in an arcing motion until arms are extended, then slowly lower back to shoulder height.",
            "default_sets": 3,
            "default_reps": "10-12"
        },
        {
            "id": "romanian_deadlift",
            "name": "Romanian Deadlift (RDL)",
            "category": "Hypertrophy",
            "target_muscle": "Hamstrings",
            "secondary_muscles": ["Glutes", "Erector Spinae"],
            "equipment": "Barbell or Dumbbells",
            "difficulty": "Intermediate",
            "instructions": "Hold weights in front of thighs with slight bend in knees. Hinge backwards at hips while keeping back neutral until feeling a deep stretch in hamstrings, then contract glutes to return to standing.",
            "default_sets": 3,
            "default_reps": "10-12"
        }
    ]

    for ex in exercises:
        doc_ref = db.collection("exercises").document(ex["id"])
        doc_ref.set(ex)
        print(f"Seeded exercise: {ex['name']}")

    sample_log = {
        "user_id": "default_user",
        "exercise_name": "Barbell Bench Press",
        "sets": [
            {"set_num": 1, "weight_lbs": 185, "reps": 8},
            {"set_num": 2, "weight_lbs": 195, "reps": 8},
            {"set_num": 3, "weight_lbs": 205, "reps": 6}
        ],
        "notes": "Solid form, felt strong on last set",
        "logged_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    db.collection("workout_logs").add(sample_log)
    print("Seeded sample workout log entry.")
    print("Seeding completed successfully!")

if __name__ == "__main__":
    seed()
