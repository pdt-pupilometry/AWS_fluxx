# Imagen base oficial de Lambda para Python en ARM64 (Graviton, mas barato)
FROM public.ecr.aws/lambda/python:3.12-arm64

# opencv-python-headless trae ffmpeg embebido (necesario para leer .mp4)
# y no depende de libGL, por lo que funciona sin librerias graficas del sistema.
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY app.py ${LAMBDA_TASK_ROOT}/

CMD ["app.lambda_handler"]
