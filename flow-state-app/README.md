# Flow State App

A local web application backed by [Fulcra](https://fulcradynamics.com) that enables musicians to record extended jam sessions and asynchronously extract semantic musical ideas via acoustic audio markers.

## Overview

Traditional recording workflows force musicians to break their creative flow to hit buttons, name files, or set markers. Flow State lets musicians play continuously: when an inspiring riff or idea occurs, the musician simply plays a predefined "audio marker" (e.g., a specific chord progression or chime). 

A background DSP pipeline analyzes the session, identifies the marker occurrences, extracts 30-second clips, computes musical metadata (Key and BPM), and indexes the ideas directly into the user's Fulcra vault as custom annotations.

## Architecture & How It Works

```
Browser (getUserMedia)
   │
   │ (WebSocket audio chunks every 2s)
   ▼
FastAPI Backend (src/app/main.py)
   │
   ├──▶ Upload Full Session to Fulcra Storage (/agent/flow-state/sessions/)
   │
   ▼
Async DSP Worker (src/worker/processor.py)
   │
   ├── 1. Audio Normalization (0 dB peak)
   ├── 2. MFCC Cross-Correlation (scipy / librosa) vs. Marker Template
   ├── 3. Peak Finding & Confidence Thresholding (≥ 94%)
   ├── 4. 30s Audio Slice Extraction & Upload to Fulcra
   ├── 5. Key Estimation (Chroma feature correlation) & BPM Tracking
   │
   ▼
Fulcra Vault (No SQL DB Required)
   ├── Data Type: MomentAnnotation/MusicalIdea
   └── Semantic Tags: #key:<Key>, #bpm:<BPM>
```

### Key Components

1. **Audio Streaming & Ingestion:**
   - Captures microphone input with echo cancellation disabled and gain preservation.
   - Streams 2-second binary audio chunks over WebSockets to disk to protect against browser crashes.
   - Saves raw audio and uploads complete sessions to Fulcra cloud storage via `fulcra-api file`.

2. **DSP Marker Detection & Feature Extraction:**
   - **Template Matching:** Normalizes audio and computes Mel-Frequency Cepstral Coefficients (MFCCs) to match the harmonic signature of the user's recorded marker against the continuous stream.
   - **Musical Analysis:** Generates chroma feature profiles to estimate musical key and tempo (BPM) on the extracted 30-second slice.
   - **Custom Annotations:** Records each idea into Fulcra as a `MusicalIdea` (`MomentAnnotation`) tagged with `#key:...` and `#bpm:...`.

3. **Semantic Querying (Zero Database):**
   - The Review UI does not scan local files or rely on an external SQL database.
   - Queries `fulcra-api catalog` dynamically to resolve the user's schema UUID and retrieves records via `fulcra-api get-records`.
   - Streams audio files back to `wavesurfer.js` via an authenticated FastAPI proxy (`/api/audio`).

## What the Skill Offers

When invoked as a Hermes / AI Agent skill (`SKILL.md`), it provides:

- **Automated Authentication Checks:** Validates active Fulcra authorization via the official `fulcra-connect` skill.
- **Idempotent Data Type Provisioning:** Runs `scripts/setup_fulcra.sh` to register the `MusicalIdea` (`MomentAnnotation`) schema in the user's Fulcra catalog if not already present.
- **Self-Contained Server Lifecycle:** `scripts/start_server.sh` automatically creates a virtual environment, installs runtime and DSP dependencies (`fastapi`, `uvicorn`, `librosa`, `scipy`, `numpy`, `websockets`, `python-multipart`), and launches the application at `http://127.0.0.1:8000`.
- **Iterate Mode:** Guides agent collaborators through `CONTEXT.md` architecture patterns when modifying DSP thresholds or UI components.

## Quick Start

### 1. Provision Fulcra Data Types
```bash
bash scripts/setup_fulcra.sh
```

### 2. Launch Application
```bash
bash scripts/start_server.sh
```

Open `http://127.0.0.1:8000` in your browser to record a marker and start a session.
