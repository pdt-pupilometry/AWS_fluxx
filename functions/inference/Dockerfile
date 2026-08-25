# Imagen base oficial de Lambda para Python en ARM64 (Graviton)
FROM public.ecr.aws/lambda/python:3.12-arm64

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# El modelo viaja DENTRO de la imagen: cold start sin descargas desde S3
# y replicación trivial (la imagen es autocontenida).
COPY model/ ${LAMBDA_TASK_ROOT}/model/
COPY app.py processor.py frame_keys.py records.py onnx_session.py yolo_onnx.py ${LAMBDA_TASK_ROOT}/

CMD ["app.lambda_handler"]
