# A4 — mudanças deliberadas

## 1. Actor/event recovery

Na A3, falhas individuais de RPC/decode podiam acionar `a2_invalidate_actor_history`, zerando o contexto do símbolo e impondo 60 s de recuperação. Na A4, esses casos chamam `a4_event_local_fault()` e o evento é descartado. O reset de fonte permanece para desconexão WebSocket, overflow da fila e defeitos de continuidade.

O writer expõe `a4_event_local_fault_ms`/`a4_event_local_faults`. O sensor_worker não converte `rpc_errors`/`decode_errors` em recovery da fonte; `dropped_events`, boundary nativo e desconexão continuam fazendo recovery.

## 2. Estado de livros

`a4/book_health.py` separa frescor do instrumento e transporte da venue. A UI possui FRESH/STALE/RECONNECTING/DISCONNECTED/INVALID. `book_max_age_ms=2000` não foi relaxado e STALE continua bloqueado para fills.

## 3. Sem mudança de estratégia

Detecção, ARM, confirmação, Economic Gate, sizing, stops/trailing e N2 heads permanecem os mesmos. A A4 altera disponibilidade/telemetria, não os thresholds que definem oportunidade.
