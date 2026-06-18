"""
Newsletter Ejecutivo GovLab — backend
  /api/generate/stream  — SSE: streaming de Claude con búsqueda web en tiempo real
  /api/context          — GET: lista archivos de Contexto/
  /api/context/{file}   — GET: lee un archivo / PUT: guarda un archivo
"""
import os, json, datetime, glob
from dotenv import load_dotenv

load_dotenv()  # carga .env antes de leer variables de entorno

from fastapi import FastAPI, Header
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import anthropic

app = FastAPI(title="Newsletter Ejecutivo GovLab")

ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTEXTO_DIR = os.path.join(ROOT, "Contexto")

# ─── Clientes Anthropic ────────────────────────────────────────────────────────
_api_key     = os.environ.get("ANTHROPIC_API_KEY", "")
async_client = anthropic.AsyncAnthropic(api_key=_api_key)

# ─── Contexto institucional ────────────────────────────────────────────────────
def load_contexto_docs() -> str:
    docs = []
    for path in sorted(glob.glob(os.path.join(CONTEXTO_DIR, "*.md"))):
        filename = os.path.basename(path)
        if filename == "00_sistema_instrucciones.md":
            continue
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read().strip()
            docs.append(f"### [{filename}]\n{content}")
        except Exception as e:
            docs.append(f"### [{filename}]\n[Error: {e}]")
    return "\n\n---\n\n".join(docs)


def build_system_prompt(ctx: str) -> str:
    prompt_path = os.path.join(CONTEXTO_DIR, "00_sistema_instrucciones.md")
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path, encoding="utf-8") as f:
                template = f.read()
            return template.replace("{ctx}", ctx)
        except Exception as e:
            print(f"Error cargando system prompt desde {prompt_path}: {e}")
    
    # Fallback en caso de que falle la lectura
    return f"Eres el redactor del newsletter ejecutivo. Contexto institucional:\n\n{ctx}"


# Cargar al arrancar
CONTEXTO_INSTITUCIONAL = load_contexto_docs()
SYSTEM_PROMPT          = build_system_prompt(CONTEXTO_INSTITUCIONAL)


# ─── Modelos ───────────────────────────────────────────────────────────────────
class Config(BaseModel):
    tipo: str = "ejecutivo"
    ejes: list[str] = []
    periodo_dias: int = 7
    num_items: int = 4
    audiencia: str = "Juan Carlos Camelo"
    notas: str = ""


class ContextUpdate(BaseModel):
    content: str


def build_user_message(cfg: Config) -> str:
    hoy  = datetime.date.today().isoformat()
    ejes = ", ".join(cfg.ejes) if cfg.ejes else "todos los ejes prioritarios"
    num  = max(1, min(20, cfg.num_items))
    return (
        f"Hoy es {hoy}. Genera un newsletter tipo '{cfg.tipo}' para: {cfg.audiencia}.\n"
        f"Ejes a cubrir: {ejes}.\n"
        f"Período: últimos {cfg.periodo_dias} días.\n"
        f"Número de ítems: {num}.\n"
        f"Notas del usuario: {cfg.notas or 'ninguna'}.\n"
        f"Busca en internet, filtra y devuelve solo el JSON válido."
    )


def extract_json(text: str) -> dict:
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start == -1:
        raise json.JSONDecodeError("No JSON found", text, 0)
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise json.JSONDecodeError("Unbalanced JSON", text, start)


# ─── Streaming endpoint ────────────────────────────────────────────────────────
@app.post("/api/generate/stream")
async def generate_stream(cfg: Config, x_api_key: str = Header(default="")):
    api_key = x_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        async def _err():
            yield f"data: {json.dumps({'type':'error','message':'Falta la clave API de Anthropic (ANTHROPIC_API_KEY)'})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    # Refrescar el cliente con la clave actual (por si cambió en runtime)
    client = anthropic.AsyncAnthropic(api_key=api_key)

    async def event_generator():
        try:
            full_text          = ""
            search_count       = 0
            current_block_type = ""
            current_tool_input = ""

            async with client.messages.stream(
                model="claude-sonnet-4-5",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 10}],
                messages=[{"role": "user", "content": build_user_message(cfg)}],
            ) as stream:
                async for event in stream:
                    etype = getattr(event, "type", "") or ""

                    if etype == "content_block_start":
                        block = getattr(event, "content_block", None)
                        current_block_type = getattr(block, "type", "") or ""
                        current_tool_input = ""
                        if current_block_type == "tool_use":
                            search_count += 1
                            yield f"data: {json.dumps({'type':'searching','count':search_count})}\n\n"

                    elif etype == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        dtype = getattr(delta, "type", "") or ""
                        if dtype == "text_delta":
                            full_text += getattr(delta, "text", "")
                            yield f"data: {json.dumps({'type':'text_chunk','total':len(full_text)})}\n\n"
                        elif dtype == "input_json_delta":
                            current_tool_input += getattr(delta, "partial_json", "")

                    elif etype == "content_block_stop":
                        if current_block_type == "tool_use" and current_tool_input:
                            try:
                                ti = json.loads(current_tool_input)
                                q  = ti.get("query", "")
                                if q:
                                    yield f"data: {json.dumps({'type':'search_query','query':q})}\n\n"
                            except Exception:
                                pass

            # Parsear JSON final
            try:
                data = extract_json(full_text)
                yield f"data: {json.dumps({'type':'done','newsletter':data})}\n\n"
            except json.JSONDecodeError as e:
                yield f"data: {json.dumps({'type':'error','message':f'JSON inválido: {e}'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type':'error','message':str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── Config status endpoint ────────────────────────────────────────────────────
@app.get("/api/config/status")
def get_config_status():
    return {"has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY", ""))}


# ─── Context file endpoints ────────────────────────────────────────────────────
@app.get("/api/context")
def list_context():
    files = []
    for path in sorted(glob.glob(os.path.join(CONTEXTO_DIR, "*.md"))):
        stat = os.stat(path)
        files.append({
            "filename": os.path.basename(path),
            "size":     stat.st_size,
            "modified": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return {"files": files}


def _safe_path(filename: str):
    """Valida que el nombre no permita path traversal y sea un .md."""
    if ".." in filename or "/" in filename or "\\" in filename:
        return None
    if not filename.endswith(".md"):
        return None
    return os.path.join(CONTEXTO_DIR, filename)


@app.get("/api/context/{filename}")
def get_context_file(filename: str):
    path = _safe_path(filename)
    if not path:
        return JSONResponse(status_code=400, content={"error": "Nombre de archivo inválido"})
    if not os.path.exists(path):
        return JSONResponse(status_code=404, content={"error": "Archivo no encontrado"})
    with open(path, encoding="utf-8") as f:
        return {"filename": filename, "content": f.read()}


@app.put("/api/context/{filename}")
def update_context_file(filename: str, body: ContextUpdate):
    global CONTEXTO_INSTITUCIONAL, SYSTEM_PROMPT
    path = _safe_path(filename)
    if not path:
        return JSONResponse(status_code=400, content={"error": "Nombre de archivo inválido"})
    with open(path, encoding="utf-8", mode="w") as f:
        f.write(body.content)
    # Recargar contexto y regenerar system prompt en caliente
    CONTEXTO_INSTITUCIONAL = load_contexto_docs()
    SYSTEM_PROMPT          = build_system_prompt(CONTEXTO_INSTITUCIONAL)
    return {"ok": True}


# ─── Frontend estático ─────────────────────────────────────────────────────────
@app.get("/")
def index():
    return FileResponse(os.path.join(ROOT, "index.html"))


app.mount("/assets", StaticFiles(directory=os.path.join(ROOT, "assets")), name="assets")
