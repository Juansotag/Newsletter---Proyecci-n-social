import os
import glob
from supabase import create_client
from dotenv import load_dotenv

# Load env vars
load_dotenv()

# Setup Supabase client
supabase_url = os.environ.get("SUPABASE_URL")
# Use secret key first (has bypass RLS), fallback to service key
supabase_key = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")

if not supabase_url or not supabase_key:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_SECRET_KEY/SUPABASE_SERVICE_KEY environment variables")

supabase = create_client(supabase_url, supabase_key)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTEXTO_DIR = os.path.join(ROOT, "Contexto")

def seed():
    search_path = os.path.join(CONTEXTO_DIR, "*.md")
    files = sorted(glob.glob(search_path))
    
    print(f"Found {len(files)} markdown files in {CONTEXTO_DIR}")
    
    docs_to_insert = []
    for filepath in files:
        filename = os.path.basename(filepath)
        
        # Read content
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Parse is_system_prompt
        is_system_prompt = (filename == "00_sistema_instrucciones.md")
        
        # Parse sort_order from prefix (e.g. "01_xxx.md" -> 1)
        sort_order = 0
        prefix = filename.split("_")[0]
        if prefix.isdigit():
            sort_order = int(prefix)
            
        doc_data = {
            "name": filename,
            "folder": "",
            "content": content,
            "description": "",
            "is_system_prompt": is_system_prompt,
            "sort_order": sort_order
        }
        docs_to_insert.append(doc_data)
        
    print(f"Upserting {len(docs_to_insert)} documents into Supabase...")
    
    # Perform upsert
    res = supabase.table("documents").upsert(docs_to_insert, on_conflict="folder,name").execute()
    
    print("Seeding completed successfully!")
    print(f"Inserted/updated {len(res.data)} documents.")

if __name__ == "__main__":
    seed()
