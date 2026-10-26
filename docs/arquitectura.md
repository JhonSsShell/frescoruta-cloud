# Documento de arquitectura — FrescoRuta Cloud

## 1. Impulsores de la arquitectura

| Impulsor | Fuente | Implicación |
|---|---|---|
| Picos de 4× en viernes y diciembre | Negocio | Escalado horizontal por servicio, servicios sin estado |
| 8 % de pedidos con faltantes | Operación | Reserva de inventario atómica antes de cobrar |
| Caídas de 2–3 h del monolito | TI | Aislamiento de fallas, health checks, Circuit Breaker |
| Despliegues manuales con ventana | TI | Contenedores, IaC (`render.yaml`), CI |
| Presupuesto de prototipo USD 0 | Restricción | Plan gratuito de Render; Redis compartido |

## 2. Estilo arquitectónico

**Microservicios** organizados por capacidades de negocio (catálogo, pedidos,
pagos, notificaciones) detrás de un **API Gateway**, con comunicación:

* **Síncrona (REST/JSON sobre HTTPS)** cuando el cliente necesita la respuesta:
  consultas y la saga de creación del pedido.
* **Asíncrona (eventos en Redis Streams)** para efectos secundarios que no deben
  bloquear al cliente: notificaciones y alertas de stock.

Cada servicio es dueño de sus datos (**Database per Service**). La consistencia
entre servicios es **eventual** y se garantiza con una **Saga orquestada**.

## 3. Vistas (modelo 4+1)

| Vista | Artefacto |
|---|---|
| Escenarios | [Casos de uso](uml/casos-de-uso.png) |
| Lógica | [Clases: pedidos y catálogo](uml/clases-pedidos-catalogo.png), [pagos y notificaciones](uml/clases-pagos-notificaciones.png), [chasis](uml/clases-chasis.png) |
| Procesos | [Secuencia de la saga 1/2](uml/secuencia-crear-pedido.png) y [2/2](uml/secuencia-crear-pedido_001.png) |
| Desarrollo | [Componentes](uml/vista-componentes.png) y estructura del repositorio |
| Física | [Despliegue en Render](uml/despliegue.png) |

## 4. Escenarios de atributos de calidad

| ID | Atributo | Estímulo | Respuesta esperada | Medida | Táctica / patrón |
|---|---|---|---|---|---|
| QA-1 | Disponibilidad | La pasarela de pagos deja de responder | Los pedidos se rechazan sin cobrar y sin bloquear stock; el catálogo sigue disponible | Tras 3 fallos el circuito se abre y la respuesta baja de ~400 ms a ~40 ms; 0 reservas huérfanas | Circuit Breaker, Retry, compensación |
| QA-2 | Consistencia | 30 pedidos concurrentes compiten por 40 kg de mora (3 kg c/u) | Solo se confirman los que caben | 13 confirmados, 17 rechazados, stock final 1 (nunca negativo) | `UPDATE … WHERE stock >= q` atómico |
| QA-3 | Rendimiento | 50 clientes consultan el catálogo sin pausa (~300 req/s) | Respuesta desde caché | p95 < 500 ms (medido: 431 ms en 2 vCPU con los 5 servicios) | Cache-Aside con TTL 30 s |
| QA-4 | Escalabilidad | Pico de eventos de pedidos | Se agregan réplicas del suscriptor | Sin duplicados ni pérdidas | Competing Consumers, consumidor idempotente |
| QA-5 | Seguridad | Cliente abusivo envía 1 000 req/min | Se limita al cliente sin afectar a otros | HTTP 429 + `Retry-After` | Token Bucket por API key |
| QA-6 | Modificabilidad | Se agrega Nequi como medio de pago | Solo se crea una estrategia nueva | 1 clase, 0 cambios en la saga | Strategy + Factory |

## 5. Decisiones relevantes

Ver [ADR](adr/). Resumen de compromisos (*trade-offs*):

* **Saga síncrona dentro de `POST /orders`**: más simple de demostrar y de razonar;
  a cambio, la latencia del pedido suma la de sus dependencias. Una versión de
  producción respondería `202 Accepted` y procesaría la saga de forma asíncrona.
* **Redis como broker, caché y registro**: un solo recurso gratuito; a cambio,
  es un punto único de falla de la plataforma (mitigado: los servicios degradan
  a modo local y el negocio sigue funcionando sin eventos).
* **SQLite por servicio**: cero costo y cero configuración; a cambio, datos
  efímeros en Render gratuito. `DATABASE_URL` permite pasar a PostgreSQL.
