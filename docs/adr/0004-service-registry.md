# ADR 0004 — Service Registry con auto-registro en Redis

* **Estado:** aceptada
* **Contexto:** en el plan gratuito de Render los web services no pueden
  recibir tráfico por la red privada y la URL pública definitiva
  (`*.onrender.com`) solo se conoce después del primer despliegue.
* **Decisión:** cada servicio publica `RENDER_EXTERNAL_URL` (o `SERVICE_URL`)
  en el hash `frescoruta:registry` al arrancar y cada 30 s
  (*Self-Registration*). Los clientes resuelven por nombre lógico con una caché
  local de 10 s (*Client-side Discovery*), con respaldo en variables
  `<SERVICIO>_URL` y valores locales.
* **Consecuencias:** (+) el Blueprint funciona sin editar URL a mano; el mismo
  código sirve en local, Compose y Render; (−) dependencia adicional de Redis
  (mitigada con el respaldo estático).
* **Alternativas:** URL fijas en variables (frágil si Render asigna sufijo),
  red privada de Render (requiere plan pago), Consul/Eureka (sobredimensionado).
