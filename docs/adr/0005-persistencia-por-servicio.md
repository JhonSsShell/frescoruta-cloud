# ADR 0005 — SQLite por servicio con migración directa a PostgreSQL

* **Estado:** aceptada (para el prototipo)
* **Contexto:** *Database per Service* exige una base por servicio; el plan
  gratuito de Render permite una sola base PostgreSQL y caduca a los 30 días.
* **Decisión:** cada servicio usa su propio archivo SQLite mediante SQLAlchemy
  2.x. La URL se configura con `DATABASE_URL`, por lo que pasar a PostgreSQL
  no requiere cambios de código. Las reservas de stock usan una actualización
  condicional atómica que funciona igual en ambos motores.
* **Consecuencias:** (+) cero costo y arranque inmediato; aislamiento real de
  datos; (−) en Render gratuito los datos se pierden al dormir el servicio
  (aceptable para la demostración; el catálogo se resiembra).
