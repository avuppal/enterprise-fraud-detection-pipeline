terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
  # Note: Credentials/config are typically inherited from environment/CLI profiles.
}

resource "aws_s3_bucket" "raw_transactions" {
  bucket = "enterprise-fraud-data-avuppal-us-east-1"

  tags = {
    Project     = "FraudDetectionPipeline"
    Owner       = "SystemAI"
    Environment = "Development"
  }
}

output "raw_data_bucket_name" {
  description = "The name of the S3 bucket for raw transaction data."
  value       = aws_s3_bucket.raw_transactions.bucket
}