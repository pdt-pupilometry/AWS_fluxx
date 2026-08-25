FROM public.ecr.aws/lambda/python:3.12-arm64

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY app.py config.py naming.py preprocess.py storage.py pipeline.py ${LAMBDA_TASK_ROOT}/

CMD ["app.lambda_handler"]
