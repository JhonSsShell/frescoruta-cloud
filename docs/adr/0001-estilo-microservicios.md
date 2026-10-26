# ADR 0001 — Estilo de microservicios con API Gateway

* **Estado:** aceptada
* **Contexto:** el monolito actual escala como una unidad, un error en
  facturación tumba la toma de pedidos y cada despliegue requiere detener todo.
  Los picos de demanda afectan principalmente a catálogo y pedidos.
* **Decisión:** separar el sistema en cuatro servicios por capacidad de negocio
  (catálogo, pedidos, pagos, notificaciones) más un API Gateway como único
  punto de entrada. Las preocupaciones transversales se empaquetan en un
  *chasis* compartido (`frescoruta_common`).
* **Consecuencias:** (+) despliegue y escalado independientes, aislamiento de
  fallas, equipos autónomos; (−) complejidad operativa, consistencia eventual y
  necesidad de observabilidad distribuida (se mitiga con `X-Request-ID`, logs
  JSON y `/api/status`).
* **Alternativas:** monolito modular (más simple, pero no resuelve el escalado
  diferenciado ni el aislamiento de fallas); *serverless* puro (acoplado al
  proveedor y con arranques en frío por función).
