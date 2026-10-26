# ADR 0003 — Redis Streams como bus de eventos

* **Estado:** aceptada
* **Contexto:** se requiere comunicación asíncrona confiable para
  notificaciones y alertas, con presupuesto cero y despliegue en Render.
* **Decisión:** usar Redis Streams (Render Key Value) con *consumer groups*:
  entrega *at-least-once*, confirmación explícita (`XACK`), relectura de
  pendientes, reclamo de mensajes huérfanos (`XAUTOCLAIM`) y cola de mensajes
  muertos tras 5 intentos. Los consumidores son idempotentes.
* **Consecuencias:** (+) un solo componente para eventos, caché y registro;
  reparto de carga entre réplicas; (−) durabilidad limitada en el plan gratuito
  (sin persistencia) y sin garantías de orden global entre particiones.
* **Alternativas:** RabbitMQ/CloudAMQP (más funciones de enrutamiento, otro
  proveedor), Kafka (excesivo para el volumen), Redis Pub/Sub (sin
  persistencia ni confirmaciones).
