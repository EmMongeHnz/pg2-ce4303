# M3 — Prueba sintética con el Stanford bunny

Valida el núcleo de reconstrucción (TSDF → malla → distancia al original) con datos cuya forma y poses se conocen, antes de usar capturas reales.

## Método

1. `tests/spikes/m3-synthetic/fetch-bunny.py` descarga el bunny (no se versiona; ver *Fuente de datos*).
2. `tests/spikes/m3-synthetic/run-bunny-test.py` lo escala a 120 mm, lo apoya sobre el plato (z hacia arriba, mm, el mismo marco que la pose del anillo ArUco) y genera una vista cada 10° de azimut a 300 mm.
   - Intrínsecas de la captura real: 480×640 en vertical, fx = fy = 434.1 (medidas en 0a).
   - La profundidad de cada vista sale de `capture_service.synthetic_depth`: se muestrea la superficie y un z-buffer elige la cara visible de cada píxel; la profundidad es la intersección exacta del rayo del centro del píxel con el plano de esa cara.
3. `astrea_common.tsdf` integra las vistas en la rejilla común: cubo de 25 cm a 192³ (1.30 mm por vóxel), truncamiento de 4 vóxeles, peso 1 por observación sin tope. Se procesa por franjas de 8 planos en z, para que los temporales quepan en una Pi 3.
4. `coordinator.meshing` extrae la superficie con marching cubes (`scikit-image`), descarta las caras en cubos con algún vóxel sin observar y conserva la componente conexa mayor.
5. `coordinator.mesh_metrics` mide distancias con `scipy` entre puntos muestreados de ambas mallas.

## Umbral (fijado antes de ver resultados)

Se mide la **exactitud** (reconstrucción → original), porque el fondo del objeto apoya en el plato y nunca se observa; un Chamfer bidireccional contra la malla completa quedaría dominado por esa zona.

- Media ≤ **2 vóxeles** (2.6 mm) y p95 ≤ **4 vóxeles** (5.2 mm).
- Se aplica a la corrida sin ruido. La **completitud** (original → reconstrucción) solo se reporta, y solo sobre las caras que alguna vista puede ver.

## Resultados

Bunny completo (69 451 triángulos), 36 vistas por anillo salvo que se indique.

| Variante | Exactitud media | Exactitud p95 | Completitud media (caras visibles) | Resultado |
|---|---|---|---|---|
| Sin ruido, elevación 35° (corrida de referencia del gate) | 0.21 mm (0.16 vóx) | 0.50 mm (0.38 vóx) | 0.21 mm | Cumple |
| Ruido gaussiano σ = 1 mm en profundidad | 0.22 mm | 0.52 mm | 0.21 mm | Cumple |
| Dos anillos (25° y 45°, 72 vistas) | 0.21 mm | 0.50 mm | 0.21 mm | Cumple |
| Ruido σ = 1 mm y dos anillos | 0.21 mm | 0.50 mm | 0.21 mm | Cumple |
| Bunny de menor resolución (`res2`) | 0.21 mm | 0.49 mm | 0.22 mm | Cumple |
| Error aleatorio de azimut σ = 0.5° en la pose | 0.21 mm | 0.50 mm | n/a | Cumple |
| Error aleatorio de azimut σ = 1° en la pose | 0.21 mm | 0.52 mm | n/a | Cumple |
| Error aleatorio de azimut σ = 3° en la pose | 0.28 mm | 0.80 mm | n/a | Cumple |
| Error aleatorio de azimut σ = 8° en la pose | 1.46 mm (1.12 vóx) | 6.36 mm (4.89 vóx) | n/a | No cumple (solo p95) |

- Con un anillo, la malla cruda tiene 50–70 componentes pequeñas (restos de superficie); la componente mayor conserva ≈ 99.7 % de las caras.
- Se ven el 83 % de las caras con un anillo y el 87 % con dos; el resto (fondo y zonas ocultas) no se puede reconstruir desde estas vistas.
- La completitud tiene máximos de 7–9 mm en zonas visibles pero estrechas (por ejemplo, entre las orejas).
- Una corrida de 36 vistas toma unos 25 s en la PC de desarrollo: ≈ 0.2 s por vista en generar la profundidad sintética y ≈ 0.4 s por vista en integrarla en la TSDF de 192³. Los arreglos de la rejilla ocupan 78 MB. La velocidad de integración en la Pi 3 queda por medir (M4).

## Límites de esta prueba

- **Piso de la métrica:** las distancias son de punto a muestra más cercana. Comparar el bunny de referencia contra sí mismo con los mismos parámetros da media 0.084 mm, p95 0.165 mm y máximo 0.40 mm, así que parte de los 0.21 mm medidos es sesgo de muestreo y no error de la reconstrucción.
- **Los errores de pose probados subestiman la sensibilidad real.** Con el objeto centrado sobre el eje del plato, girar en azimut desliza la superficie sobre sí misma, por eso 1° casi no mueve los números. Errores de traslación o de elevación sí mueven la superficie a lo largo de su normal. Estas filas no demuestran que un error ArUco < 1° sea suficiente: eso se verifica con capturas reales.

- La profundidad sintética es perfecta salvo el ruido gaussiano; no modela los bordes con píxeles espurios ni los sesgos del TrueDepth.
- Los errores de pose probados son aleatorios y solo en azimut (ver arriba); tampoco cubren una deriva sistemática.
- No hay plato ni fondo: el RANSAC del plato y el recorte cilíndrico (M3, preprocesado por vista) no se ejercitan aquí.
- El umbral de 2 vóxeles quedó muy holgado frente al resultado (0.16 vóxeles). Puede apretarse al valor que se quiera vigilar como regresión.

## Fuente de datos

Stanford Bunny, reconstrucción por *zippering* (Turk y Levoy, SIGGRAPH 1994), 35 947 vértices y 69 451 triángulos.
Fuente: *Stanford University Computer Graphics Laboratory* (https://graphics.stanford.edu/data/3Dscanrep/). Según la página, los modelos pueden usarse para investigación y redistribuirse gratis, citando la fuente.
