"""
Newsletter Ejecutivo GovLab — backend
  /api/generate/stream  — SSE: streaming de Claude con búsqueda web en tiempo real
  /api/context          — GET: lista archivos de Contexto/
  /api/context/{file}   — GET: lee un archivo / PUT: guarda un archivo
"""
import os, json, datetime, glob, re as _re
from dotenv import load_dotenv

load_dotenv()  # carga .env antes de leer variables de entorno

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import anthropic
from supabase import create_client

app = FastAPI(title="Newsletter Ejecutivo GovLab")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ─── Clientes Anthropic ────────────────────────────────────────────────────────
import httpx
_api_key     = os.environ.get("ANTHROPIC_API_KEY", "")
async_client = anthropic.AsyncAnthropic(api_key=_api_key, http_client=httpx.AsyncClient(verify=False))

# ─── Cliente Supabase ─────────────────────────────────────────────────────────
_supabase_url = os.environ.get("SUPABASE_URL", "")
_supabase_key = os.environ.get("SUPABASE_SECRET_KEY", "") or os.environ.get("SUPABASE_SERVICE_KEY", "")
supabase_client = None
if _supabase_url and _supabase_key:
    import httpx
    from supabase import ClientOptions
    # Se desactiva la verificación SSL (verify=False) para evitar errores causados por proxies corporativos (ej. Zscaler)
    _options = ClientOptions(httpx_client=httpx.Client(verify=False))
    supabase_client = create_client(_supabase_url, _supabase_key, options=_options)

# ─── Contexto institucional (Supabase) ──────────────────────────────────────────
def resolve_doc_references(content: str, already_loading: str = "") -> str:
    """
    Reemplaza referencias @nombre_doc.md dentro del contenido de un documento
    por el contenido completo del doc referenciado (un solo nivel, sin recursión).

    - Si el doc referenciado no existe: deja texto [Referencia no encontrada: ...]
    - Si el doc se referencia a sí mismo: lo ignora silenciosamente.
    - Docs con tag_context='excluded' no se resuelven aunque sean referenciados.
    """
    if not supabase_client:
        return content

    refs = _re.findall(r'@([\w\-\.]+\.md)', content)
    if not refs:
        return content

    for ref_name in set(refs):
        if ref_name == already_loading:
            content = content.replace(
                f"@{ref_name}",
                f"[auto-referencia ignorada: {ref_name}]"
            )
            continue
        try:
            resp = supabase_client.table("documents").select(
                "name, content, description, folder, tag_context"
            ).eq("name", ref_name).eq("is_system_prompt", False).execute()

            if resp.data:
                doc = resp.data[0]
                # No resolver docs excluidos aunque sean referenciados explícitamente
                if (doc.get("tag_context") or "always").lower() == "excluded":
                    content = content.replace(
                        f"@{ref_name}",
                        f"[Documento excluido del contexto: {ref_name}]"
                    )
                    continue
                ref_desc = doc.get("description", "").strip()
                ref_cont = doc.get("content", "").strip()
                ref_fold = doc.get("folder", "")
                ref_path = f"{ref_fold}/{ref_name}" if ref_fold else ref_name
                use_line = f"USO: {ref_desc}\n" if ref_desc else ""
                injected = f"\n\n#### [Referenciado: {ref_path}]\n{use_line}{ref_cont}\n"
                content  = content.replace(f"@{ref_name}", injected)
            else:
                content = content.replace(
                    f"@{ref_name}",
                    f"[Referencia no encontrada: {ref_name}]"
                )
        except Exception as e:
            print(f"Error resolviendo referencia @{ref_name}: {e}")

    return content


def load_contexto_docs_db() -> str:
    if not supabase_client:
        return ""
    try:
        response = supabase_client.table("documents").select(
            "folder, name, content, description, tag_context"
        ).eq("is_system_prompt", False).order("sort_order").execute()
        docs = []
        for doc in response.data:
            tag = (doc.get("tag_context") or "always").strip().lower()
            if tag == "excluded":
                continue

            folder      = doc.get("folder", "")
            name        = doc.get("name", "")
            content     = doc.get("content", "").strip()
            description = doc.get("description", "").strip()

            # Resolver referencias @nombre_doc.md dentro del contenido
            content = resolve_doc_references(content, already_loading=name)

            path     = f"{folder}/{name}" if folder else name
            use_line = f"USO: {description}\n" if description else ""
            docs.append(f"### [{path}]\n{use_line}{content}")
        return "\n\n---\n\n".join(docs)
    except Exception as e:
        print(f"Error cargando documentos de contexto desde Supabase: {e}")
        return ""


def build_system_prompt_db(ctx: str) -> str:
    if not supabase_client:
        return f"Eres el redactor del newsletter ejecutivo. Contexto institucional:\n\n{ctx}"
    try:
        response = supabase_client.table("documents").select("content").eq("is_system_prompt", True).execute()
        if response.data:
            template = response.data[0]["content"]
            return template.replace("{ctx}", ctx)
    except Exception as e:
        print(f"Error cargando system prompt desde Supabase: {e}")
    
    return f"Eres el redactor del newsletter ejecutivo. Contexto institucional:\n\n{ctx}"


# ─── Modelos válidos (whitelist) ─────────────────────────────────────────────
VALID_MODELS = {"claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6"}
VALID_ASSIST_MODELS = {"claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6"}


# ─── Modelos ───────────────────────────────────────────────────────────────────
class Config(BaseModel):
    tipo: str = "ejecutivo"
    ejes: list[str] = []
    periodo_dias: int = 7
    num_items: int = 4
    audiencia: str = "Juan Carlos Camelo"
    notas: str = ""
    model: str = "claude-sonnet-4-6"
    buscar_web: bool = True
    usar_contexto: bool = True


class DocCreate(BaseModel):
    folder: str = ""
    name: str
    content: str = ""
    description: str = ""


class DocUpdate(BaseModel):
    folder: str = None
    name: str = None
    content: str = None
    description: str = None
    sort_order: int = None


class AssistRequest(BaseModel):
    name: str
    content: str
    instruction: str
    model: str = "claude-haiku-4-5-20251001"


class AssistResponse(BaseModel):
    response: str
    modified_content: str


def build_user_message(cfg: Config) -> str:
    hoy  = datetime.date.today().isoformat()
    ejes = ", ".join(cfg.ejes) if cfg.ejes else "todos los ejes prioritarios"
    num  = max(1, min(20, cfg.num_items))
    search_instr = "Busca en internet, filtra y devuelve solo el JSON válido." if cfg.buscar_web else "Devuelve solo el JSON válido basándote únicamente en las notas proporcionadas por el usuario."
    return (
        f"Hoy es {hoy}. Genera un newsletter tipo '{cfg.tipo}' para: {cfg.audiencia}.\n"
        f"Ejes a cubrir: {ejes}.\n"
        f"Período: últimos {cfg.periodo_dias} días.\n"
        f"Número de ítems: {num}.\n"
        f"Notas del usuario: {cfg.notas or 'ninguna'}.\n"
        f"{search_instr}"
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
    client = anthropic.AsyncAnthropic(api_key=api_key, http_client=httpx.AsyncClient(verify=False))

    # Cargar contexto y prompt del sistema dinámicamente desde Supabase
    if cfg.usar_contexto:
        context_docs = load_contexto_docs_db()
    else:
        context_docs = "No se incluye contexto institucional. Genera el boletín basándote únicamente en la información provista en las notas del usuario."
        
    system_prompt = build_system_prompt_db(context_docs)

    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 10}] if cfg.buscar_web else []

    async def event_generator():
        try:
            full_text          = ""
            search_count       = 0
            current_block_type = ""
            current_tool_input = ""

            model = cfg.model if cfg.model in VALID_MODELS else "claude-sonnet-4-6"
            async with client.messages.stream(
                model=model,
                max_tokens=4096,
                system=system_prompt,
                tools=tools,
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


# ─── Document CRUD endpoints (Supabase) ─────────────────────────────────────────
@app.get("/api/docs")
def list_docs():
    if not supabase_client:
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    try:
        response = supabase_client.table("documents").select("id, folder, name, description, is_system_prompt, sort_order, updated_at").order("sort_order").execute()
        return {"files": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/docs/{doc_id}")
def get_doc(doc_id: str):
    if not supabase_client:
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    try:
        response = supabase_client.table("documents").select("*").eq("id", doc_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
        return response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/docs")
def create_doc(body: DocCreate):
    if not supabase_client:
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    try:
        data = body.dict()
        
        # Calculate sort order based on maximum sort_order + 1
        max_sort_response = supabase_client.table("documents").select("sort_order").order("sort_order", desc=True).limit(1).execute()
        sort_order = 0
        if max_sort_response.data:
            sort_order = max_sort_response.data[0]["sort_order"] + 1
            
        data["sort_order"] = sort_order
        data["is_system_prompt"] = False
        
        response = supabase_client.table("documents").insert(data).execute()
        if not response.data:
            raise HTTPException(status_code=500, detail="Error al crear el documento")
        return response.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/docs/{doc_id}")
def update_doc(doc_id: str, body: DocUpdate):
    if not supabase_client:
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    try:
        update_data = {k: v for k, v in body.dict().items() if v is not None}
        if not update_data:
            raise HTTPException(status_code=400, detail="No se enviaron campos para actualizar")
            
        response = supabase_client.table("documents").update(update_data).eq("id", doc_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Documento no encontrado o no actualizado")
        return response.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/docs/{doc_id}")
def delete_doc(doc_id: str):
    if not supabase_client:
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    try:
        check_response = supabase_client.table("documents").select("is_system_prompt").eq("id", doc_id).execute()
        if not check_response.data:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
        if check_response.data[0]["is_system_prompt"]:
            raise HTTPException(status_code=400, detail="No se puede eliminar el prompt del sistema")
            
        supabase_client.table("documents").delete().eq("id", doc_id).execute()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/docs/assist", response_model=AssistResponse)
async def assist_doc(body: AssistRequest, x_api_key: str = Header(default="")):
    api_key = x_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="Falta la clave API de Anthropic (ANTHROPIC_API_KEY)")
        
    client = anthropic.AsyncAnthropic(api_key=api_key, http_client=httpx.AsyncClient(verify=False))
    
    system_prompt = (
        "Eres un asistente experto de inteligencia artificial del GovLab.\n"
        "Estás ayudando al usuario a redactar, revisar o editar un documento de contexto de un newsletter ejecutivo.\n"
        "El usuario te enviará el contenido actual del documento, el nombre del documento y su instrucción.\n"
        "Tú debes responder en formato JSON con dos campos:\n"
        "1. 'response': Tu respuesta explicativa o de revisión de seguridad para el usuario. Debe ser en español, formal y ejecutivo, SIN EMOJIS.\n"
        "Si el usuario pide validar si los cambios están bien, analiza de manera crítica el contenido.\n"
        "Si el documento es '00_sistema_instrucciones.md' y detectas que la estructura JSON de la salida fue alterada de tal forma que no cumpla con las especificaciones obligatorias, advierte claramente en 'response' que esa modificación dañará el funcionamiento y parser del newsletter, y NO modifiques el contenido.\n"
        "La estructura obligatoria del JSON que Claude debe retornar en el newsletter es:\n"
        "{\n"
        "  \"titulo\": \"...\",\n"
        "  \"fecha\": \"YYYY-MM-DD\",\n"
        "  \"contexto\": \"...\",\n"
        "  \"cifras\": [{\"dato\": \"...\", \"contexto\": \"...\", \"fuente\": \"...\", \"url\": \"...\"}],\n"
        "  \"items\": [{\"eje\": \"...\", \"titular\": \"...\", \"resumen\": \"...\", \"por_que_importa\": \"...\", \"fuente\": \"...\", \"url\": \"...\"}],\n"
        "  \"oportunidades\": [{\"texto\": \"...\", \"fuente\": \"...\", \"url\": \"...\"}]\n"
        "}\n"
        "2. 'modified_content': Si la instrucción solicita cambios, mejoras, traducciones o agregar información, devuelve aquí el contenido del documento completamente actualizado. Si es una pregunta de revisión o no requiere cambios, devuelve el contenido original tal cual.\n\n"
        "Devuelve exclusivamente el JSON válido, sin textos adicionales, prefijos ni marcas de código."
    )
    
    user_message = (
        f"Nombre del documento: {body.name}\n\n"
        f"Contenido actual:\n{body.content}\n\n"
        f"Instrucción del usuario: {body.instruction}"
    )
    
    try:
        assist_model = body.model if body.model in VALID_ASSIST_MODELS else "claude-haiku-4-5-20251001"
        message = await client.messages.create(
            model=assist_model,
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}]
        )
        
        text = message.content[0].text
        data = extract_json(text)
        
        response_text = data.get("response", "")
        modified_content = data.get("modified_content", body.content)
        
        return AssistResponse(response=response_text, modified_content=modified_content)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al conectar con Claude: {str(e)}")


# ─── Frontend estático ─────────────────────────────────────────────────────────
@app.get("/")
def index():
    return FileResponse(os.path.join(ROOT, "index.html"))


app.mount("/assets", StaticFiles(directory=os.path.join(ROOT, "assets")), name="assets")
