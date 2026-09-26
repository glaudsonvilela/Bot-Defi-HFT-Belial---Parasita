# Mapa do repositório

```text
/
├── README.md
├── .gitignore
├── assets/
│   └── bot-belial-banner.png
├── docs/
│   ├── CURRENT_STATE.md
│   └── REPOSITORY_MAP.md
└── Belial Negro/
    └── Raydium Hunter/
        ├── source-snapshots/
        │   ├── belial-a3-90m-v1/
        │   ├── belial-a4-90m-v1/
        │   └── belial-a4-90m-v1-fix1/
        ├── installers/
        └── manifests/
            ├── installer-origin.tsv
            ├── evidence-index.tsv
            ├── runtime-index.tsv
            ├── release-index.tsv
            ├── backup-index.tsv
            └── SHA256SUMS
```

## O que fica no GitHub

Código-fonte, testes, scripts de instalação, configuração não secreta, documentação e índices necessários para reconstruir o estado técnico.

## O que continua no SSD

Artefatos de compilação `target-*`, RAW, runtime, bancos, evidência bruta e backups completos.

Esses dados **não são apagados**. O GitHub contém índices deles para que sua existência e a sequência histórica continuem visíveis sem transformar o repositório de código em um depósito de build/runtime.

