# Newsletter Ejecutivo — GovLab · Universidad de La Sabana

App web que arma un newsletter ejecutivo personalizado: el usuario configura
tipo, ejes temáticos, período y número de ítems; el agente busca en internet
con Claude, filtra lo relevante y entrega una edición lista para descargar como
PDF (una página, optimizada para celular).

El comportamiento del agente (qué busca, dónde, en qué fuentes y redes, y el
system prompt) está definido en **`CONTEXTO_NEWSLETTER.md`**. Esa es la fuente
de verdad; si cambia lo que necesita el destinatario, se edita ahí.

---

## Arrancar en local

```bash
pip install -r backend/requirements.txt
cp .env.example .env          # y poner la ANTHROPIC_API_KEY
uvicorn backend.main:app --reload --port 8000
# Abrir http://localhost:8000
```

> El `index.html` también abre directo en el navegador (doble clic) y funciona
> en **modo demo** con un newsletter de ejemplo, sin backend ni llaves. Útil
> para presentar la interfaz de inmediato.

## Variables de entorno

Copiar `.env.example` como `.env` y completar:
- `ANTHROPIC_API_KEY` — obligatoria, obtener en console.anthropic.com

## Deploy en Railway

1. Conectar el repositorio de GitHub a Railway.
2. Cargar `ANTHROPIC_API_KEY` en las variables del dashboard de Railway.
3. Railway detecta Python y usa el `Procfile` automáticamente. (Dominio:
   CNAME en Cloudflare apuntando a `*.up.railway.app`.)

## Assets de marca

Los logos y la fuente van en `assets/` (ver `assets/README.txt`). Mientras no
estén, el frontend muestra un *fallback* de texto y se ve bien igual.

---

## Continuar el desarrollo con Antigravity

Abre esta carpeta en Antigravity y pega este mensaje:

> Esta app ya funciona: `index.html` (frontend completo con el design system del
> GovLab, configurador y modo demo) y `backend/main.py` (FastAPI que sirve el
> frontend y expone `/api/generate`, el cual llama a Claude con la herramienta
> de búsqueda web y devuelve el newsletter en JSON). El comportamiento del
> agente está en `CONTEXTO_NEWSLETTER.md` y su system prompt ya está copiado en
> `backend/main.py` — no lo cambies, solo edítalo en ambos sitios si hace falta.
> Los assets de marca van en `assets/` y NO debes regenerarlos.
>
> Construye UNA cosa a la vez, en este orden:
> 1. Verifica que `/api/generate` parsea bien la respuesta de Claude cuando hay
>    bloques de búsqueda web intercalados; maneja el caso de JSON con texto
>    alrededor de forma robusta.
> 2. Mejora la exportación a PDF: reemplaza `window.print()` por una descarga
>    con `html2pdf.js` que respete el encabezado azul y los ítems sin cortes.
> 3. Agrega un historial simple de ediciones generadas (en memoria del navegador
>    primero; Supabase solo si se decide persistir).
>
> No cambies la paleta de colores ni las fuentes. No crees subcarpetas nuevas
> hasta que la funcionalidad lo exija.

---

Laboratorio de Gobierno · Universidad de La Sabana
