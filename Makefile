.PHONY: init-lambda tf-apply clean all

LAMBDA_DIR := lambda
JIRA_DIR := $(LAMBDA_DIR)/jira_issue
CREATE_USER_DIR := $(LAMBDA_DIR)/create-user

init-lambda:
	@echo "Building jira_issue lambda..."
	@ ( \
		cd "$(JIRA_DIR)" && \
		rm -rf build.zip __pycache__ *.pyc *.pyo && \
		python3 -m pip install -r requirements.txt -t . && \
		zip -r -q build.zip . -x "__pycache__/*" "*.pyc" "*.pyo" ".DS_Store" \
	)
	@echo "Building create-user lambda..."
	@ ( \
		cd "$(CREATE_USER_DIR)" && \
		rm -rf build.zip __pycache__ *.pyc *.pyo && \
		zip -r -q build.zip . -x "__pycache__/*" "*.pyc" "*.pyo" ".DS_Store" \
	)

tf-apply:
	terraform init
	terraform apply

clean:
	rm -rf \
		"$(JIRA_DIR)/build.zip" "$(JIRA_DIR)/__pycache__" \
		"$(CREATE_USER_DIR)/build.zip" "$(CREATE_USER_DIR)/__pycache__"

all: init-lambda tf-apply
