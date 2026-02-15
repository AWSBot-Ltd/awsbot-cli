import boto3
import typer
from botocore.exceptions import ClientError
from rich import print

app = typer.Typer()


@app.command()
def create_secret(
    name: str = typer.Argument(..., help="The name of the secret"),
    value: str = typer.Argument(..., help="The secret string/value"),
    description: str = typer.Option(
        None, "--desc", "-d", help="Description of the secret"
    ),
    region: str = typer.Option("us-east-1", help="AWS Region"),
):
    """Create a new secret in AWS Secrets Manager."""
    client = boto3.client("secretsmanager", region_name=region)

    try:
        response = client.create_secret(
            Name=name, Description=description or "", SecretString=value
        )
        print(
            f"[green]Success![/green] Secret created: [bold]{response['Name']}[/bold]"
        )
        print(f"ARN: {response['ARN']}")

    except ClientError as e:
        print(f"[red]Error:[/red] {e.response['Error']['Message']}")
        raise typer.Exit(code=1)
