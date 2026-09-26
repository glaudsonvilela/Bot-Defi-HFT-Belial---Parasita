# A5.1R Recovery FIX1

Base: 8cb7dcb98e50dc102816999c2d5f558876758166

- preserva accumulated_active_ms do run ativo;
- A5.1R canônico e Rust ficam byte-idênticos;
- recovery no boot vira DEFERRED_STREAM_AUDIT em O(1);
- novo segmento inicia antes do audit multi-GiB;
- audit final é streaming linha por linha;
- não cria cópia de inputs.jsonl;
- seg-01 original permanece intocado;
- posições PAPER não atravessam gap entre segmentos;
- orders=false.
