# Plan técnico — Newsletter Ejecutivo GovLab v2

De app con `.md` en disco → herramienta con persistencia en Supabase, gestor de
documentos en carpetas, historial de reportes y envíos programados.

Documento de referencia. Pensado para ejecutarse **una etapa a la vez** en
Antigravity. Cada etapa es funcional por sí sola y deja la app en un estado
desplegable.

---

## 0. Diagnóstico de la base actual

| Pieza | Hoy | Problema |
|---|---|---|
| `backend/main.py` | FastAPI: `/api/generate/stream` (SSE + web search), `/api/context*` (lee/escribe `Contexto/*.md` en disco), sirve `index.html` y `/assets`. | Las escrituras a disco **no persisten en Railway** (filesystem efímero: cada redeploy/reinicio revierte al repo). |
| `Contexto/*.md` | 11 archivos; `00_sistema_instrucciones.md` es la plantilla con `{ctx}`. | El `_safe_path` rechaza `/`, así que **no hay carpetas**. |
| `index.html` | 3 pestañas: Newsletter, Editor de Contexto, Configuración. PDF vía `window.print()`. | Sin historial, sin envío, sin render de markdown, sin árbol de carpetas. |

La columna vertebral está bien hecha (SSE robusto, parseo de JSON con bloques de
búsqueda intercalados, design system limpio). **No se reescribe**: se extiende.

---

## 1. Arquitectura objetivo

```
Navegador (index.html)
   │  fetch /api/*
   ▼
FastAPI (Railway, Pro)  ──► Claude API (web_search)
   │                    ──► Resend / SMTP (envío)
   │  supabase-py (service key, solo en backend)
   ▼
Supabase (Postgres, Pro)
   ├─ documents   (reemplaza Contexto/*.md)
   ├─ reports     (historial)
   └─ schedules   (envíos programados)

Railway Cron Job  ──► python -m backend.run_due  (cada 15 min)
   └─ genera, renderiza, envía y registra los schedules vencidos
```

**Principio de seguridad:** la `SUPABASE_SERVICE_KEY` y la `ANTHROPIC_API_KEY`
viven **solo** en variables de entorno del backend. El navegador nunca toca
Supabase directo; todo pasa por FastAPI. Como es una herramienta interna de un
solo usuario, RLS puede quedar permisivo (el backend es el único cliente).

---

## 2. Esquema Supabase (SQL — correr una vez en el editor SQL)

```sql
-- Documentos de contexto (reemplazan Contexto/*.md)
create table documents (
  id               uuid primary key default gen_random_uuid(),
  folder           text not null default '',          -- '' = raíz; 'perfiles' o 'perfiles/camelo'
  name             text not null,                      -- '01_universidad.md'
  content          text not null default '',
  description      text not null default '',           -- pista para el agente: cuándo/para qué usar este doc
  is_system_prompt boolean not null default false,     -- el doc-plantilla con {ctx}
  sort_order       int not null default 0,
  updated_at       timestamptz not null default now(),
  unique (folder, name)
);

-- Historial de reportes generados
create table reports (
  id             uuid primary key default gen_random_uuid(),
  created_at     timestamptz not null default now(),
  titulo         text,
  origen         text not null default 'manual',       -- 'manual' | 'programado'
  config         jsonb not null,                       -- la Config con la que se generó
  newsletter     jsonb not null,                       -- el JSON que devolvió Claude (fuente de verdad)
  search_queries jsonb not null default '[]'           -- trazabilidad de búsquedas
);

-- Envíos programados
create table schedules (
  id         uuid primary key default gen_random_uuid(),
  name       text not null,
  config     jsonb not null,                           -- qué newsletter generar
  email_to   text not null,                            -- coma-separado si varios
  cron       text not null,                            -- '0 7 * * 1' = lunes 7:00
  active     boolean not null default true,
  last_run   timestamptz,
  next_run   timestamptz,
  created_at timestamptz not null default now()
);

create index on reports (created_at desc);
create index on schedules (active, next_run);
```

Notas:
- `gen_random_uuid()` y `jsonb` vienen por defecto en Supabase.
- El **HTML no se guarda**: se re-renderiza desde `newsletter` (jsonb) con la
  misma función `renderNewsletter` del front. Una sola fuente de verdad, menos
  peso, y el render evoluciona sin migrar datos viejos.

---

## 3. Contratos de endpoints (lo que expone el backend)

### Documentos
| Método | Ruta | Cuerpo / Notas |
|---|---|---|
| GET | `/api/docs` | Lista todos: `{id, folder, name, description, is_system_prompt, sort_order, updated_at}`. El front arma el árbol. |
| GET | `/api/docs/{id}` | Devuelve el doc completo con `content`. |
| POST | `/api/docs` | `{folder, name, content, description}` → crea. |
| PUT | `/api/docs/{id}` | `{folder?, name?, content?, description?}` → actualiza parcial. |
| DELETE | `/api/docs/{id}` | Borra (bloquear si `is_system_prompt`). |

### Reportes
| Método | Ruta | Notas |
|---|---|---|
| GET | `/api/reports` | Lista liviana (sin `newsletter` completo): `{id, created_at, titulo, origen, config}`. |
| GET | `/api/reports/{id}` | Reporte completo (incluye `newsletter` y `search_queries`). |
| DELETE | `/api/reports/{id}` | Borra. |

El guardado del reporte ocurre **dentro del backend** al terminar la
generación (no se confía al navegador): cuando el stream termina con el JSON
válido, antes de emitir `done`, se hace el `insert` en `reports`.

### Schedules
| Método | Ruta | Notas |
|---|---|---|
| GET | `/api/schedules` | Lista. |
| POST | `/api/schedules` | `{name, config, email_to, cron}` → calcula `next_run`. |
| PUT | `/api/schedules/{id}` | Actualiza (recalcula `next_run` si cambia `cron`). |
| DELETE | `/api/schedules/{id}` | Borra. |
| POST | `/api/schedules/{id}/run` | Disparo manual ("enviar ahora") para probar. |

---

## 4. El system prompt mejorado (tu punto 3)

Hoy `build_system_prompt` mete todos los docs en `{ctx}` sin decirle al agente
para qué sirve cada uno. El cambio:

1. El system prompt deja de ser un archivo en disco y pasa a ser el doc con
   `is_system_prompt = true`. Editarlo en la pestaña = editar ese registro.
2. `load_contexto_docs()` arma el bloque `{ctx}` así, **inyectando la
   `description` de cada doc** como instrucción de uso:

```
### [01_universidad-la-sabana.md]
USO: Datos institucionales base. Cítalo para cifras de la universidad, no para noticias externas.
<contenido…>

---

### [04_camelo-agenda-estrategica.md]
USO: Prioridades del director. Úsalo para decidir el ángulo "por qué importa" de cada ítem.
<contenido…>
```

Así, en el system prompt puedes escribir reglas del tipo *"para el campo
`por_que_importa`, cruza la noticia con la agenda estratégica del doc marcado
USO: prioridades del director"* y el agente sabe a cuál te refieres. La
`description` la editas por documento en el árbol.

---

## 5. Etapas de ejecución (una a la vez en Antigravity)

> Mensaje base para Antigravity (pégalo arriba en cada etapa):
> *"App existente: `index.html` (front completo con design system GovLab, 3
> pestañas, render del newsletter y SSE) y `backend/main.py` (FastAPI). No
> reescribas la base ni cambies paleta ni fuentes. Construye SOLO la etapa que
> te indico, déjala funcional y desplegable, y no crees subcarpetas nuevas
> salvo las que la etapa exija."*

### Etapa 0 — Supabase conectado + migración de los `.md`
- Crear proyecto Supabase, correr el SQL de la sección 2.
- Añadir a Railway: `SUPABASE_URL ()`, `SUPABASE_SERVICE_KEY ()` o `SUPABASE_SECRET_KEY ()`.
- Añadir `supabase>=2.0` a `requirements.txt`.
- Script único `backend/seed_docs.py` que lee los `Contexto/*.md` actuales y los
  inserta en `documents`: `00_sistema_instrucciones.md` → `is_system_prompt=true`;
  el prefijo numérico (`01_`, `02_`…) → `sort_order`; `folder=''`; `description=''`.
- Criterio de cierre: `select count(*) from documents` = 11.

### Etapa 1 — Documentos en Supabase + carpetas + render markdown
- Reemplazar `/api/context*` por `/api/docs*` (sección 3) leyendo de Supabase.
- Reescribir `load_contexto_docs()` y `build_system_prompt()` para leer de la DB
  e inyectar la `description` como `USO:` (sección 4). Quitar el `_safe_path` de
  disco.
- Front, pestaña "Editor de Contexto": árbol de carpetas (agrupar por `folder`),
  acciones crear / renombrar / mover / eliminar doc y crear carpeta. Campo
  `description` editable por doc. Botón "Vista previa" que renderiza el markdown
  con `marked.js` (CDN) junto al editor.
- El system prompt aparece como un doc especial fijado arriba (no borrable).
- Criterio de cierre: editar un doc, redeploy, y el cambio **sigue ahí**.

### Etapa 2 — Historial + PDF real
- Backend: al cerrar el stream con JSON válido, `insert` en `reports`
  (`origen='manual'`) antes del evento `done`; devolver el `id` en `done`.
- Reemplazar `window.print()` por `html2pdf.js` (respeta el encabezado azul y
  evita cortar ítems), tanto en el reporte recién generado como en el historial.
- Front, nueva pestaña "Historial": lista desde `/api/reports` (fecha, título,
  tipo, ejes, período); al hacer clic, traer `/api/reports/{id}` y re-renderizar
  con `renderNewsletter`; botones "Descargar PDF" y "Eliminar".
- Criterio de cierre: generar 2 reportes, recargar la página, verlos en el
  historial y descargar el PDF de uno viejo.

### Etapa 3 — Envío programado
- Backend: CRUD `/api/schedules` (sección 3) + `next_run` calculado desde el
  `cron` (usar `croniter`). Refactor: extraer la generación a una función
  reutilizable sin streaming (`generate_once(config) -> (newsletter, queries)`)
  que tanto el stream como el envío usan.
- `backend/run_due.py`: consulta schedules `active` con `next_run <= now()`,
  por cada uno genera, renderiza HTML, envía email, registra `report`
  (`origen='programado'`) y actualiza `last_run`/`next_run`.
- Railway: crear un **Cron Job** que ejecute `python -m backend.run_due` cada
  15 min (decisión de infra, ver sección 6).
- Email: integrar Resend (ver sección 6). Render de email = HTML de una columna
  con CSS inline + PDF adjunto.
- Front, nueva pestaña "Envío": formulario (nombre, reusar config de newsletter,
  correo destino, frecuencia → cron con presets "semanal lunes 7am", etc.),
  lista de programaciones activas con last/next run, y botón "Enviar ahora"
  (`POST /api/schedules/{id}/run`) para probar sin esperar.
- Criterio de cierre: "Enviar ahora" deja el correo en bandeja y crea un reporte
  `programado` en el historial.

---

## 6. Decisiones de infraestructura que tomas tú (bloquean solo la Etapa 3)

Las etapas 0–2 no dependen de esto. Para la 3 hay tres bifurcaciones reales:

**a) ¿Quién dispara los envíos?**
- *Recomendado* — **Railway Cron Job** separado (`run_due` cada 15 min).
  Desacoplado del web, sobrevive si el web duerme, una sola corrida sin
  carreras. Encaja con que ya estás en Railway Pro.
- *Más rápido de montar* — **APScheduler** en proceso dentro de FastAPI. Sirve
  si el servicio web está siempre activo, pero se complica con múltiples
  instancias.

**b) ¿Con qué se envía el correo?**
- *Recomendado* — **Resend**: API REST simple, ~3.000 correos/mes gratis, buena
  entregabilidad y verificación de dominio fácil en Cloudflare (que ya usas).
- Alternativa — SMTP de Outlook/365: lo conoces, pero fricción con app-password
  / OAuth y entregabilidad más frágil para correo "automático".

**c) ¿PDF en el correo programado?**
- El PDF de las etapas 1–2 se hace en el navegador (`html2pdf.js`). En el envío
  programado no hay navegador, así que para adjuntar PDF server-side se necesita
  **WeasyPrint** (HTML→PDF sin navegador headless).
- *v1 más simple*: enviar solo el HTML inline en el correo y dejar el PDF para
  la vista web. Añades WeasyPrint después si el destinatario lo pide.

Mi recomendación de arranque: **Railway Cron + Resend + HTML inline (sin PDF
adjunto en v1)**. Es lo más robusto y ligero; el PDF server-side se suma luego.

---

## 7. Orden sugerido y dependencias

```
Etapa 0 ──► Etapa 1 ──► Etapa 2 ──► Etapa 3
(Supabase)  (docs+carpetas)  (historial)  (envío)
                                   │
                            decisiones 6a/6b/6c
```

Las etapas 0→2 te dan ya el 70% del valor (persistencia real + carpetas +
system prompt con `USO:` + historial con PDF) y no requieren ninguna decisión de
infra. La etapa 3 entra cuando definas a/b/c.
