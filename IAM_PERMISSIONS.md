# Permisos IAM necesarios (usuario/rol del `.env`)

El `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` que carga `deploy.sh` **no** es
el rol que ejecutan las Lambdas (esos ya están definidos y acotados dentro de
`template.yaml`) — es el usuario/rol que corre `sam build`/`sam deploy` y
necesita permisos para crear la infraestructura en sí. La siguiente policy de
least-privilege alcanza para desplegar este stack; reemplaza `<REGION>`,
`<ACCOUNT_ID>` y `<STACK_NAME>` por tus valores reales (o un prefijo con
`*` si vas a reutilizar el mismo usuario para varios stacks):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudFormationStack",
      "Effect": "Allow",
      "Action": [
        "cloudformation:CreateStack",
        "cloudformation:UpdateStack",
        "cloudformation:DeleteStack",
        "cloudformation:DescribeStacks",
        "cloudformation:DescribeStackEvents",
        "cloudformation:DescribeStackResource",
        "cloudformation:DescribeStackResources",
        "cloudformation:GetTemplate",
        "cloudformation:GetTemplateSummary",
        "cloudformation:ListStackResources",
        "cloudformation:CreateChangeSet",
        "cloudformation:DescribeChangeSet",
        "cloudformation:ExecuteChangeSet",
        "cloudformation:DeleteChangeSet",
        "cloudformation:ListChangeSets"
      ],
      "Resource": [
        "arn:aws:cloudformation:<REGION>:<ACCOUNT_ID>:stack/<STACK_NAME>/*",
        "arn:aws:cloudformation:<REGION>:<ACCOUNT_ID>:stack/aws-sam-cli-managed-default/*",
        "arn:aws:cloudformation:<REGION>:<ACCOUNT_ID>:stack/<STACK_NAME>-*-CompanionStack/*"
      ]
    },
    {
      "Sid": "SamTransform",
      "Effect": "Allow",
      "Action": "cloudformation:CreateChangeSet",
      "Resource": "arn:aws:cloudformation:<REGION>:aws:transform/Serverless-2016-10-31"
    },
    {
      "Sid": "SamArtifactsAndAppBuckets",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:DeleteBucket",
        "s3:PutBucketNotification",
        "s3:GetBucketNotification",
        "s3:PutLifecycleConfiguration",
        "s3:GetLifecycleConfiguration",
        "s3:PutEncryptionConfiguration",
        "s3:GetEncryptionConfiguration",
        "s3:PutBucketVersioning",
        "s3:GetBucketVersioning",
        "s3:PutBucketPolicy",
        "s3:GetBucketPolicy",
        "s3:DeleteBucketPolicy",
        "s3:PutBucketPublicAccessBlock",
        "s3:GetBucketPublicAccessBlock",
        "s3:PutBucketTagging",
        "s3:GetBucketTagging",
        "s3:TagResource",
        "s3:UntagResource",
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:ListBucket",
        "s3:GetBucketLocation"
      ],
      "Resource": [
        "arn:aws:s3:::aws-sam-cli-managed-*",
        "arn:aws:s3:::<STACK_NAME>-videos-<ACCOUNT_ID>",
        "arn:aws:s3:::<STACK_NAME>-videos-<ACCOUNT_ID>/*",
        "arn:aws:s3:::<STACK_NAME>-frames-<ACCOUNT_ID>",
        "arn:aws:s3:::<STACK_NAME>-frames-<ACCOUNT_ID>/*"
      ]
    },
    {
      "Sid": "EcrImageRepo",
      "Effect": "Allow",
      "Action": [
        "ecr:CreateRepository",
        "ecr:DeleteRepository",
        "ecr:DescribeRepositories",
        "ecr:SetRepositoryPolicy",
        "ecr:GetRepositoryPolicy",
        "ecr:DeleteRepositoryPolicy",
        "ecr:PutLifecyclePolicy",
        "ecr:GetLifecyclePolicy",
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:PutImage",
        "ecr:BatchGetImage",
        "ecr:TagResource",
        "ecr:UntagResource",
        "ecr:ListTagsForResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "LambdaFunctions",
      "Effect": "Allow",
      "Action": [
        "lambda:CreateFunction",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
        "lambda:GetFunction",
        "lambda:GetFunctionConfiguration",
        "lambda:DeleteFunction",
        "lambda:AddPermission",
        "lambda:RemovePermission",
        "lambda:PutFunctionConcurrency",
        "lambda:TagResource",
        "lambda:ListTags"
      ],
      "Resource": "arn:aws:lambda:<REGION>:<ACCOUNT_ID>:function:<STACK_NAME>-*"
    },
    {
      "Sid": "StepFunctions",
      "Effect": "Allow",
      "Action": [
        "states:CreateStateMachine",
        "states:UpdateStateMachine",
        "states:DeleteStateMachine",
        "states:DescribeStateMachine",
        "states:TagResource"
      ],
      "Resource": "arn:aws:states:<REGION>:<ACCOUNT_ID>:stateMachine:<STACK_NAME>-pipeline"
    },
    {
      "Sid": "EventBridgeRule",
      "Effect": "Allow",
      "Action": [
        "events:PutRule",
        "events:PutTargets",
        "events:RemoveTargets",
        "events:DeleteRule",
        "events:DescribeRule"
      ],
      "Resource": "arn:aws:events:<REGION>:<ACCOUNT_ID>:rule/<STACK_NAME>-*"
    },
    {
      "Sid": "IamRolesForFunctionsAndStateMachine",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:GetRole",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:PassRole",
        "iam:TagRole"
      ],
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/<STACK_NAME>-*"
    }
  ]
}
```

## Notas

- `sam deploy --resolve-s3`/`--resolve-image-repos` crean automáticamente el
  bucket de artefactos (`aws-sam-cli-managed-*`) y el repo ECR — de ahí que el
  usuario necesite permisos para crearlos, no solo para usarlos.
  `CloudFormation` genera nombres de rol e IDs de recursos con el prefijo del
  `StackName`, por eso casi todo se puede acotar con `<STACK_NAME>-*` en vez
  de `Resource: "*"`.
- `--resolve-s3` crea un **segundo stack de CloudFormation** separado,
  llamado siempre `aws-sam-cli-managed-default` (el que aloja el bucket de
  artefactos), sin importar el `<STACK_NAME>` de tu app. Por eso el statement
  `CloudFormationStack` tiene que incluir ambos ARNs de stack — solo dar
  permiso sobre `<STACK_NAME>` tira `AccessDenied: cloudformation:CreateChangeSet`
  sobre `stack/aws-sam-cli-managed-default/*`.
- `--resolve-image-repos` crea un **tercer stack**, el "Companion Stack" que
  gestiona el repo ECR: `<STACK_NAME>-<hash>-CompanionStack` (el `<hash>` es
  un sufijo generado por SAM). De ahí el patrón con wildcard
  `<STACK_NAME>-*-CompanionStack/*` en el `Resource`.
- El uso de `Transform: AWS::Serverless-2016-10-31` en `template.yaml`
  requiere además `cloudformation:CreateChangeSet` sobre el recurso especial
  de macro `arn:aws:cloudformation:<REGION>:aws:transform/Serverless-2016-10-31`
  (nota: el "account id" de ese ARN es literalmente `aws`, no el tuyo) — sin
  este permiso el changeset falla con
  `AccessDenied ... on resource: arn:...:transform/Serverless-2016-10-31`.
- El bucket de bootstrap (`SamCliSourceBucket`) se crea con versioning,
  encryption, public access block, tags y bucket policy. La policy S3 de
  arriba incluye esas acciones (`PutBucketVersioning`,
  `PutBucketPublicAccessBlock`, `PutBucketPolicy`, `TagResource`, etc.); si
  faltan, el create falla y el stack queda en `ROLLBACK_COMPLETE`.
- Si un deploy falla a mitad de camino por permisos, el stack de bootstrap
  (`aws-sam-cli-managed-default`) puede quedar en `REVIEW_IN_PROGRESS`,
  `ROLLBACK_COMPLETE` o `ROLLBACK_FAILED`. SAM CLI se niega a reusarlo —
  hay que borrarlo (`aws cloudformation delete-stack --stack-name
  aws-sam-cli-managed-default`) para que el próximo `sam deploy` lo cree de
  cero ya con los permisos correctos.
- Esta policy asume que reusas siempre el mismo `<STACK_NAME>`. Si vas a
  desplegar stacks con nombres distintos frecuentemente, es más simple usar
  wildcards más amplios (`<STACK_NAME>*` → `*`) a costa de perder
  least-privilege.
- Todos los ARNs con `<REGION>` son específicos de esa región — si cambias de
  región (por ejemplo porque la cuota de concurrencia de Lambda ya está
  aprobada en otra), hay que actualizar todos los `<REGION>` de esta policy
  también.
- **Como managed policy, no inline**: pegar esta policy directamente en la
  pestaña "Permissions" de un usuario IAM (inline policy) falla con "exceeds
  the non-whitespace character limit of 2048" — ese límite de 2048
  caracteres es una cuota fija de AWS para inline policies de usuario. La
  solución es crearla en **IAM → Policies → Create policy** (managed policy,
  límite de 6144 caracteres) y después adjuntarla al usuario desde
  **Permissions → Attach policies directly**.
- Verificación rápida de que el usuario tiene lo necesario:
  `aws sts get-caller-identity` (ya lo corre `deploy.sh` al inicio) confirma
  credenciales válidas; los errores de permisos insuficientes aparecen recién
  durante `sam deploy` como `AccessDenied` en el recurso puntual que falte.
