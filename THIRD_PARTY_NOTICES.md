# Third-party notices

## SmolLM2-360M-Instruct

Bottom Browser downloads `SmolLM2-360M-Instruct.Q4_K_M.gguf` on first use
from the QuantFactory GGUF repository on Hugging Face. The original model is by
Hugging Face (`HuggingFaceTB/SmolLM2-360M-Instruct`) and is licensed under the
Apache License 2.0. The downloaded file is about 258 MiB and is accepted only
when its SHA-256 digest matches the value pinned in `ai_client.py`.

- Original model: https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct
- GGUF source: https://huggingface.co/QuantFactory/SmolLM2-360M-Instruct-GGUF
- Full license text: `licenses/Apache-2.0.txt`

## llama.cpp and llama-cpp-python

Local inference uses `llama-cpp-python`, Python bindings for `llama.cpp`.
Both projects are available under the MIT License. The bundled binding's
copyright and full license text are in
`licenses/llama-cpp-python-MIT.txt`; llama.cpp's are in
`licenses/llama-cpp-MIT.txt`.

- https://github.com/abetlen/llama-cpp-python
- https://github.com/ggml-org/llama.cpp