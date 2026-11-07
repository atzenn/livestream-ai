#!/usr/bin/env bash
# Download VieNeu-TTS v3 Turbo + MOSS codec model files with curl.
#
# Why curl and not the SDK's huggingface_hub download:
# - huggingface_hub stalls on this machine's egress proxy (0 bytes
#   in 8 minutes on the 396 MB backbone file), while curl fetches
#   the same files in under a minute.
# - The codec ONNX files reference external .data files. When the
#   SDK loads them from the Hugging Face cache, ONNX Runtime resolves
#   the snapshot symlinks to blob paths, and the external-data file
#   (a different blob) fails validation ("External data path escapes
#   model directory"). Replacing the snapshot symlinks with real
#   copies fixes it. The SDK factory does not forward `codec_dir`
#   to the engine, so the cache fix is needed even though we also
#   keep local copies in models/moss-codec/ for reference.
#
# Usage: bash scripts/setup_models.sh
# Downloads ~540 MB into models/vieneu-onnx/ and models/moss-codec/,
# plus the Piper fallback voice into models/piper/ (optional, ~63 MB).
# All files are free (Apache-2.0 / MIT) and public on Hugging Face.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/models/vieneu-onnx" "$ROOT/models/moss-codec" "$ROOT/models/piper"

echo "== VieNeu-TTS v3 Turbo ONNX (fp32, onnx_update) =="
cd "$ROOT/models/vieneu-onnx"
for f in config.json tokenizer.json vieneu_acoustic_cached.onnx \
         vieneu_backbone_shared.data vieneu_decode_step.onnx \
         vieneu_prefill.onnx vieneu_v3_heads.npz; do
  curl -sL -O "https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo/resolve/main/onnx_update/$f"
  echo "ok $f"
done

echo "== MOSS audio codec ONNX =="
cd "$ROOT/models/moss-codec"
for f in codec_browser_onnx_meta.json moss_audio_tokenizer_decode_full.onnx \
         moss_audio_tokenizer_decode_shared.data moss_audio_tokenizer_decode_step.onnx \
         moss_audio_tokenizer_encode.onnx moss_audio_tokenizer_encode.data; do
  curl -sL -O "https://huggingface.co/OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX/resolve/main/$f"
  echo "ok $f"
done

echo "== Hugging Face cache: populate codec snapshot, then de-symlink =="
"$ROOT/.venv/bin/python" -c "
from huggingface_hub import hf_hub_download
files = ['moss_audio_tokenizer_decode_full.onnx',
         'moss_audio_tokenizer_decode_shared.data',
         'moss_audio_tokenizer_decode_step.onnx',
         'codec_browser_onnx_meta.json',
         'moss_audio_tokenizer_encode.onnx',
         'moss_audio_tokenizer_encode.data']
for fn in files:
    print('cached', hf_hub_download(repo_id='OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX', filename=fn))
" 2>&1 | grep -v "Warning:"
python3 - << 'PYEOF'
import os
from pathlib import Path
base = Path.home() / ".cache/huggingface/hub"
for snap in base.glob("models--OpenMOSS-Team--MOSS-Audio-Tokenizer-Nano-ONNX/snapshots/*"):
    for entry in snap.iterdir():
        if entry.is_symlink():
            target = entry.resolve()
            data = target.read_bytes()
            entry.unlink()
            entry.write_bytes(data)
            print("de-symlinked", entry.name)
PYEOF

echo "== Piper fallback voice (vi_VN, 25hours_single, low) =="
echo "Note: the vivos x-low voice 404s on the Hub despite the tree"
echo "listing (repo-side inconsistency, checked 07/11/2026)."
cd "$ROOT/models/piper"
"$ROOT/.venv/bin/python" -c "
from huggingface_hub import hf_hub_download
import shutil, os
for fn in ['vi/vi_VN/25hours_single/low/vi_VN-25hours_single-low.onnx',
           'vi/vi_VN/25hours_single/low/vi_VN-25hours_single-low.onnx.json']:
    p = hf_hub_download(repo_id='rhasspy/piper-voices', filename=fn)
    shutil.copy(p, os.path.basename(fn))
    print('ok', os.path.basename(fn))
" 2>&1 | grep -v "Warning:"

echo "== done =="
du -sh "$ROOT/models"/*
