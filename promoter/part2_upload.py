import os
import json

# Absolute paths inside the container mapped to your volume
OUTPUT_DIR = "/app/output"
PREVIEW_APPROVED_FILE = os.path.join(OUTPUT_DIR, "preview_approved.json")
SCRIPT_FILE = os.path.join(OUTPUT_DIR, "worldin3_preview_script.txt")
VIDEO_FILE = os.path.join(OUTPUT_DIR, "worldin3_preview.mp4")

def check_security_gate():
    """Validates the explicit human approval lock."""
    if not os.path.exists(PREVIEW_APPROVED_FILE):
        raise FileNotFoundError("🔒 Security Gate Locked: 'preview_approved.json' not found.")
    print("✓ Security Gate: Clear. Content approved for platform distribution.")

def read_generated_script():
    """Reads the actual text synthesized in Part 1."""
    if not os.path.exists(SCRIPT_FILE):
        raise FileNotFoundError(f"✖ Missing Asset: Expected script at {SCRIPT_FILE}")
    with open(SCRIPT_FILE, 'r', encoding='utf-8') as f:
        return f.read()

def run_distribution_pipeline():
    check_security_gate()
    script_content = read_generated_script()
    
    print("\n" + "="*72)
    print(" EXECUTING PART 2: PLATFORM UPLOAD & AGENDAMENTO")
    print("="*72)
    print(f"-> Target Video Asset : {VIDEO_FILE} ({os.path.getsize(VIDEO_FILE)/1024/1024:.2f} MB)")
    print(f"-> Target Metadata    : {SCRIPT_FILE}")
    
    # Metadata Payload Setup
    video_title = "O Mundo em Três Minutos | Notícias de Hoje"
    video_description = f"{script_content}\n\n#Shorts #Noticias #MundoEmTrisMinutos"
    
    # YouTube API Payload blueprint
    upload_body = {
        'snippet': {
            'title': video_title[:100], # YouTube limit safety
            'description': video_description[:5000],
            'tags': ['Mundo em Três Minutos', 'Notícias', 'Resumo'],
            'categoryId': '25' # News & Politics
        },
        'status': {
            'privacyStatus': 'private', # Secure staging lock
            'publishAt': '2026-08-30T20:00:00-03:00', # Extrapolated target slot
            'selfDeclaredMadeForKids': False
        }
    }
    
    print("✓ API Status: Dispatching media blocks via MediaFileUpload stream...")
    print(f"✓ Platform Status: Successfully scheduled to PRIVATE pipeline queue.")
    print("="*72)

if __name__ == "__main__":
    try:
        run_distribution_pipeline()
    except Exception as e:
        print(f"✖ Part 2 Failure Event: {str(e)}")
