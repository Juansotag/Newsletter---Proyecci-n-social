# Cambios pendientes — Newsletter GovLab
## Documento para Antigravity

---

## Estado actual del repo

La infraestructura de envío está mayormente implementada:
- `backend/email_render.py` — renderiza el JSON del newsletter como HTML de email
- `backend/run_due.py` — script de cron job que genera y envía schedules vencidos
- `backend/main.py` — endpoints `/api/schedules` CRUD + `_send_email` vía Resend
- `index.html` — pestaña "Envío" con formulario de nueva programación y lista de schedules

Quedan **tres cambios** para que todo funcione.

---

## Cambio 1 — Corrección del remitente en Resend (dominio compartido)

El placeholder `newsletter@tudominio.com` aparece en dos archivos. Resend
permite enviar **sin dominio propio** usando su dominio compartido. El remitente
correcto para cuentas sin dominio verificado es:

```
onboarding@resend.dev
```

### 1a. Variable de entorno en Railway

Añadir en el dashboard de Railway → Variables de entorno:

```
RESEND_API_KEY   = <tu API key de Resend>
RESEND_FROM_EMAIL = onboarding@resend.dev
```

No hay que tocar código si `RESEND_FROM_EMAIL` se configura como variable de
entorno. Pero el fallback en el código también debe actualizarse para que
funcione en local sin el `.env`.

### 1b. `backend/main.py` — cambiar el fallback

Buscar la línea:
```python
from_email  = os.environ.get("RESEND_FROM_EMAIL", "newsletter@tudominio.com")
```
Reemplazar por:
```python
from_email  = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
```

### 1c. `backend/run_due.py` — cambiar el fallback

Buscar la línea:
```python
_from_email    = os.environ.get("RESEND_FROM_EMAIL", "newsletter@tudominio.com")
```
Reemplazar por:
```python
_from_email    = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
```

---

## Cambio 2 — SQL para las tablas `schedules` y `reports` en Supabase

Estas tablas no existen todavía. Correr en el editor SQL de Supabase:

```sql
-- Historial de reportes generados
create table if not exists reports (
  id             uuid primary key default gen_random_uuid(),
  created_at     timestamptz not null default now(),
  titulo         text,
  origen         text not null default 'manual',
  config         jsonb not null default '{}',
  newsletter     jsonb not null default '{}',
  search_queries jsonb not null default '[]'
);

create index if not exists reports_created_at_idx on reports (created_at desc);

-- Envíos programados
create table if not exists schedules (
  id         uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  name       text not null,
  config     jsonb not null default '{}',
  email_to   text not null,
  cron       text not null,
  active     boolean not null default true,
  last_run   timestamptz,
  next_run   timestamptz
);

create index if not exists schedules_active_next_run_idx on schedules (active, next_run);
```

---

## Cambio 3 — Cron Job en Railway

`run_due.py` ya está implementado y funciona. Solo falta configurar el disparo
automático en Railway para que se ejecute cada 15 minutos.

### Pasos en el dashboard de Railway

1. Abrir el proyecto en Railway.
2. En la barra lateral, hacer clic en **"+ New"** → **"Cron Job"**.
3. Configurar:
   - **Schedule:** `*/15 * * * *`
   - **Command:** `python -m backend.run_due`
   - **Service:** el mismo servicio donde corre el backend (para heredar las
     variables de entorno `ANTHROPIC_API_KEY`, `SUPABASE_URL`,
     `SUPABASE_SECRET_KEY`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`).
4. Guardar. Railway ejecutará el script cada 15 minutos y procesará cualquier
   schedule cuyo `next_run` ya haya vencido.

### Verificación

Para probar sin esperar el cron, usar el botón "Enviar ahora" en la pestaña
Envío de la app (llama a `POST /api/schedules/{id}/run`). Si el correo llega
a `@unisabana.edu.co`, todo está funcionando.

---

## Nota sobre `run_due.py` y referencias circulares

`run_due.py` tiene su propia copia de `load_contexto_docs_db` que llama a
`resolve_doc_references` con el parámetro antiguo `already_loading=name`.
Cuando se aplique el Cambio 1 del documento anterior (pila de visitados), hay
que actualizar también esa llamada en `run_due.py`:

```python
# Antes (en run_due.py, dentro del loop de docs):
# (no llama a resolve_doc_references — aún no está integrado)

# Después — añadir esta línea antes de append:
cont = resolve_doc_references(cont, loading_stack=[name])
```

E importar la función al inicio de `run_due.py`:
```python
from backend.main import resolve_doc_references
```

Esto asegura que los envíos programados también resuelven referencias `@doc`
igual que la generación manual.

---

## Resumen de lo que hace cada cambio

| Cambio | Archivo | Qué habilita |
|---|---|---|
| 1a | Railway env vars | Resend funciona con dominio compartido |
| 1b | `backend/main.py` | Fallback correcto en local |
| 1c | `backend/run_due.py` | Fallback correcto en local |
| 2 | Supabase SQL | Tablas `reports` y `schedules` disponibles |
| 3 | Railway dashboard | Cron job dispara `run_due` cada 15 min |
