# Data source to get the latest Amazon Linux 2 AMI
data "aws_ami" "amazon_linux_2" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-hvm-*-x86_64-gp2"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "web_server" {
  ami           = data.aws_ami.amazon_linux_2.id
  instance_type = "t2.micro"

  # Use default subnet in default VPC
  subnet_id                   = sort(data.aws_subnets.default.ids)[0]
  associate_public_ip_address = true

  # Security group allowing SSH and HTTP
  vpc_security_group_ids = [aws_security_group.web.id]

  key_name = "sample-project"

  tags = {
    Name = "${var.project}-instance"
  }
}

# Security group for the instance
resource "aws_security_group" "web" {
  name_prefix = "${var.project}-ec2-web-sg"
  description = "Security group for web instance"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project}-ec2-web-sg"
  }
}
