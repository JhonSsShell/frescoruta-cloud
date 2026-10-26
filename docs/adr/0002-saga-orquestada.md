# ADR 0002 — Saga orquestada para crear pedidos

* **Estado:** aceptada
* **Contexto:** crear un pedido toca tres bases de datos (pedidos, catálogo,
  pagos). No es viable una transacción distribuida 2PC entre servicios
  independientes y en la nube.
* **Decisión:** el servicio de pedidos orquesta la saga
  T1 *reservar stock* → T2 *cobrar* → T3 *confirmar reserva*, con
  compensaciones C1 *liberar stock* y C2 *reembolsar*. Cada paso se persiste
  en una bitácora (`saga_steps`). Todas las operaciones remotas son
  idempotentes para tolerar reintentos.
* **Consecuencias:** (+) flujo explícito, fácil de leer, probar y monitorear;
  estado del pedido siempre consultable; (−) el orquestador concentra lógica y
  es dependiente de los demás servicios (mitigado con Circuit Breaker y Retry).
* **Alternativa descartada:** saga coreografiada por eventos: menos acoplamiento,
  pero el flujo queda disperso y es difícil de seguir y depurar en un equipo
  pequeño.
