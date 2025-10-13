# aws-production-support-sample-terraform


## Run

- `make init-lambda`: To Setup Lambda Funciton Code and deps
- `make tf-apply`: To init and apply TF (require approval in terminal)
- `make all`: To run all together


## call lambda function
```
export FUNCTION_URL="https://your-api-id.execute-api.your-region.amazonaws.com/prod/users"


curl -X POST "$FUNCTION_URL" \
-H "Content-Type: application/json" \
-d '{
    "username": "mdoe",
    "first_name": "Manjeet",
    "last_name": "Doe"
    }'
```
