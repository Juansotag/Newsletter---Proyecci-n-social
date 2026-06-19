# Cambios a implementar — Newsletter GovLab
## Documento para Antigravity

---

## Estado actual del repo (punto de partida)

`backend/main.py` ya tiene:
- Supabase integrado (`create_client` con `SUPABASE_URL` / `SUPABASE_SERVICE_KEY`)
- `load_contexto_docs_db()` — carga todos los docs de la tabla `documents` donde
  `is_system_prompt = false`, los formatea con `USO: {description}` y los concatena
- `build_system_prompt_db(ctx)` — carga el doc con `is_system_prompt = true` y
  reemplaza `{ctx}` con el bloque de documentos
- CRUD completo `/api/docs*`
- Endpoint `/api/docs/assist` para el chatbot del editor de contexto

**Problemas a corregir en este paquete de cambios:**
1. Los nombres de modelo están desactualizados (`claude-3-5-sonnet-latest`,
   `claude-3-5-haiku-latest`, etc.) — la API devuelve 404 con esos strings.
2. El doc `09_unisabana_programas.md` pesa ~60 000 tokens y se inyecta en cada
   llamada, disparando el costo.
3. No existe un sistema para que los documentos se referencien entre sí.

---

## Cambio 1 — Corregir nombres de modelo en `backend/main.py`

### Strings actuales (incorrectos) → strings correctos

| Lugar en el código | Valor actual | Valor correcto |
|---|---|---|
| `Config.model` default | `"claude-3-5-sonnet-latest"` | `"claude-sonnet-4-6"` |
| `AssistRequest.model` default | `"claude-3-5-sonnet-latest"` | `"claude-haiku-4-5-20251001"` |
| `client.messages.stream(model=...)` | `cfg.model or "claude-3-5-sonnet-latest"` | whitelist + fallback (ver abajo) |
| `client.messages.create(model=...)` en `/api/docs/assist` | `body.model or "claude-3-5-sonnet-latest"` | whitelist + fallback (ver abajo) |

### Implementación recomendada

En `generate_stream`, reemplazar la línea `model=cfg.model or "..."` por:

```python
VALID_MODELS = {"claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6"}
model = cfg.model if cfg.model in VALID_MODELS else "claude-sonnet-4-6"
```

Y pasarlo al stream: `model=model,`

En `assist_doc`, reemplazar la línea `model=body.model or "..."` por:

```python
VALID_ASSIST_MODELS = {"claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6"}
assist_model = body.model if body.model in VALID_ASSIST_MODELS else "claude-haiku-4-5-20251001"
```

Y pasarlo al create: `model=assist_model,`

La whitelist hace que cualquier string inválido que Antigravity pudiera haber
puesto en algún momento haga fallback limpio en lugar de explotar con 404.

---

## Cambio 2 — Columna `tag_context` en Supabase para excluir el doc de 60k tokens

El doc `09_unisabana_programas.md` es un catálogo de 386 programas académicos
con precios y códigos SNIES. No aporta nada al agente que busca noticias en
internet, y pesa ~60 000 tokens — el equivalente a unas 10 generaciones de
newsletter malgastadas por cada request.

La solución es una columna `tag_context` en la tabla `documents` con dos valores
posibles: `'always'` (default) y `'excluded'`. La lógica de cuándo usar cada
documento vive en el system prompt y en el campo `description` de cada doc
(que ya existe y el usuario ya puede editar desde la UI). El backend solo filtra
los `excluded`.

### SQL — correr una vez en el editor SQL de Supabase

```sql
-- Añadir la columna (si ya existe no falla)
alter table documents
  add column if not exists tag_context text not null default 'always';

-- Excluir el catálogo de programas
update documents
  set tag_context = 'excluded'
  where name = '09_unisabana_programas.md';
```

### Cambio en `load_contexto_docs_db` en `backend/main.py`

Modificar el `.select(...)` para traer también `tag_context`, y añadir el filtro
dentro del loop:

```python
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

            name        = doc.get("name", "")
            content     = doc.get("content", "").strip()
            description = doc.get("description", "").strip()
            folder      = doc.get("folder", "")

            # Resolver referencias @nombre_doc.md (ver Cambio 3)
            content = resolve_doc_references(content, already_loading=name)

            path     = f"{folder}/{name}" if folder else name
            use_line = f"USO: {description}\n" if description else ""
            docs.append(f"### [{path}]\n{use_line}{content}")

        return "\n\n---\n\n".join(docs)
    except Exception as e:
        print(f"Error cargando documentos de contexto desde Supabase: {e}")
        return ""
```

**Nota:** la llamada `resolve_doc_references(content, already_loading=name)` es
del Cambio 3. Si se implementan en orden, poner primero el Cambio 3 o dejar
esa línea comentada hasta implementarlo.

---

## Cambio 3 — Sistema de referencias `@nombre_doc.md` entre documentos

### Qué es y para qué sirve

Dentro del contenido de cualquier documento el usuario puede escribir:

```
@08_organigrama-unisabana-abril-2026.md
```

y el backend lo reemplaza, al construir el system prompt, por el contenido
completo de ese documento. Esto permite que documentos como la agenda
estratégica de Juan Carlos instruyan al agente de forma contextual. Ejemplo
real en `04_juan-carlos-camelo-agenda-estrategica.md`:

```
Si la búsqueda encuentra una noticia relacionada con una facultad específica
(Medicina, Derecho, Ingeniería, etc.), identifica qué área o proyecto de la
Dirección General tiene intersección consultando la estructura institucional:

@08_organigrama-unisabana-abril-2026.md
```

Así el organigrama (8 000 tokens) solo entra al contexto cuando el doc de
agenda lo referencia, y el usuario controla eso editando el texto del doc
desde la UI — sin tocar código.

### Función a añadir en `backend/main.py`

Añadir **antes** de `load_contexto_docs_db`:

```python
import re as _re

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
                doc      = resp.data[0]
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
```

### Comportamiento

| Referencia escrita en un doc | Resultado en el system prompt |
|---|---|
| `@08_organigrama-unisabana-abril-2026.md` | Contenido completo del organigrama inyectado en ese punto |
| `@doc-inexistente.md` | Texto `[Referencia no encontrada: doc-inexistente.md]` |
| `@09_unisabana_programas.md` | Texto `[Documento excluido del contexto: ...]` — el excluido no entra ni por referencia |
| Un doc referenciándose a sí mismo | Ignorado silenciosamente |

---

## Cambio 4 — Selectores de modelo en `index.html`

Reemplazar las opciones del `<select id="modelGenSelect">`:

```html
<select id="modelGenSelect" ...>
  <option value="claude-sonnet-4-6">Claude Sonnet 4.6 (Recomendado)</option>
  <option value="claude-opus-4-6">Claude Opus 4.6 (Más potente, más costo)</option>
  <option value="claude-haiku-4-5-20251001">Claude Haiku 4.5 (Más económico)</option>
</select>
```

Reemplazar las opciones del `<select id="modelAssistSelect">`:

```html
<select id="modelAssistSelect" ...>
  <option value="claude-haiku-4-5-20251001">Claude Haiku 4.5 (Recomendado — económico)</option>
  <option value="claude-sonnet-4-6">Claude Sonnet 4.6</option>
</select>
```

También reemplazar todos los defaults en el JS que dicen
`|| 'claude-3-5-sonnet-latest'` o cualquier variante de modelo viejo:
- Los que afectan al generador → `|| 'claude-sonnet-4-6'`
- Los que afectan al asistente → `|| 'claude-haiku-4-5-20251001'`

---

## Descriptions sugeridas para actualizar en Supabase

Editar el campo `description` de estos dos docs directamente en la UI
(Editor de Contexto → seleccionar doc → campo Descripción):

**`08_organigrama-unisabana-abril-2026.md`**
> Usa este documento cuando necesites identificar qué área, dirección o proyecto
> de la universidad tiene relación con el tema de una noticia. Por ejemplo: si
> aparece una noticia sobre salud o medicina, busca aquí si hay una facultad o
> proyecto H2/H3 con intersección. También úsalo cuando el newsletter deba
> mencionar responsables de área o la estructura de la Dirección General de
> Proyección Social y Co-Creación.

**`10_pei-universidad-la-sabana.md`**
> Usa este documento cuando el newsletter aborde temas de identidad institucional,
> misión, visión, valores o posicionamiento estratégico de la universidad. También
> cuando necesites enmarcar una oportunidad en el lenguaje oficial de la institución.

---

## Orden de ejecución recomendado

1. **SQL en Supabase** (Cambio 2) — añadir columna y marcar el doc 09 como excluded.
2. **`backend/main.py`** — aplicar Cambios 1, 2 y 3 en ese orden.
3. **`index.html`** — aplicar Cambio 4.
4. **Descriptions en UI** — actualizar los docs 08 y 10 desde el Editor de Contexto.
5. Redeploy en Railway y verificar que una generación de newsletter no explota con 404 y que el log de tokens baja significativamente.
