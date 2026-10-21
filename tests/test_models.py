from collections.abc import Iterator
from sqlite3 import Connection

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Dataset, DatasetField, ImportHistory, ImportIssue, Record, User


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Connection, _record: object) -> None:
        del _record
        connection.execute("PRAGMA foreign_keys = ON")

    Base.metadata.create_all(engine)
    with Session(engine) as database:
        yield database
    engine.dispose()


def test_datasets_keep_distinct_field_schemas_and_json_records(
    session: Session,
) -> None:
    customers = Dataset(
        name="Customers",
        source_filename="customers.xlsx",
        sheet_name="Customers",
        record_count=1,
        fields=[
            DatasetField(
                key="customer_id",
                source_name="Customer ID",
                display_name="顧客番号",
                position=0,
            )
        ],
        records=[
            Record(
                source_row_number=2,
                data={"customer_id": "000123", "active": True},
            )
        ],
    )
    orders = Dataset(
        name="Orders",
        source_filename="orders.xlsx",
        sheet_name="Orders",
        record_count=1,
        fields=[
            DatasetField(
                key="total",
                source_name="税込合計",
                display_name="合計",
                position=0,
                is_searchable=False,
            )
        ],
        records=[
            Record(source_row_number=7, data={"total": 1250.5, "note": None})
        ],
    )
    session.add_all([customers, orders])
    session.commit()

    stored_datasets = session.scalars(select(Dataset).order_by(Dataset.name)).all()
    assert [dataset.name for dataset in stored_datasets] == ["Customers", "Orders"]
    assert [field.key for field in stored_datasets[0].fields] == ["customer_id"]
    assert [field.key for field in stored_datasets[1].fields] == ["total"]
    assert stored_datasets[0].records[0].data == {
        "customer_id": "000123",
        "active": True,
    }
    assert stored_datasets[1].records[0].data == {"total": 1250.5, "note": None}


def test_user_email_is_normalized_and_roles_are_stored(session: Session) -> None:
    viewer = User(
        email="  VIEWER@Example.com ",
        password_hash="secure-password-hash",
        role="viewer",
    )
    importer = User(
        email="importer@example.com",
        password_hash="another-secure-hash",
        role="importer",
    )
    session.add_all([viewer, importer])
    session.commit()

    assert viewer.email == "viewer@example.com"
    assert session.scalar(select(User).where(User.role == "importer")) is not None


def test_user_email_is_unique_without_case_sensitivity(session: Session) -> None:
    session.execute(
        text(
            "INSERT INTO users (email, password_hash, role) "
            "VALUES ('user@example.com', 'hashed-one', 'viewer')"
        )
    )
    session.commit()

    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO users (email, password_hash, role) "
                "VALUES ('USER@example.com', 'hashed-two', 'viewer')"
            )
        )

    session.rollback()


def test_field_key_and_position_are_unique_per_dataset(session: Session) -> None:
    dataset = Dataset(
        name="Example",
        source_filename="example.xlsx",
        sheet_name="Sheet1",
        fields=[
            DatasetField(key="name", source_name="Name", display_name="名前", position=0),
            DatasetField(key="name", source_name="Alias", display_name="別名", position=1),
        ],
    )
    session.add(dataset)

    with pytest.raises(IntegrityError):
        session.commit()

    session.rollback()


def test_import_history_retains_failure_without_dataset(session: Session) -> None:
    failed_import = ImportHistory(
        dataset_id=None,
        source_filename="broken.xlsx",
        sheet_name="Sheet1",
        status="failed",
        failure_count=1,
        error_summary="The workbook could not be read.",
        issues=[
            ImportIssue(
                source_row_number=3,
                message="Excel row 3 contains an invalid value.",
            )
        ],
    )
    session.add(failed_import)
    session.commit()

    stored_import = session.get(ImportHistory, failed_import.id)
    assert stored_import is not None
    assert stored_import.dataset_id is None
    assert stored_import.status == "failed"
    assert stored_import.failure_count == 1
    assert stored_import.issues[0].source_row_number == 3


def test_deleting_dataset_cascades_to_fields_and_records(session: Session) -> None:
    dataset = Dataset(
        name="Example",
        source_filename="example.xlsx",
        sheet_name="Sheet1",
        fields=[
            DatasetField(key="name", source_name="Name", display_name="名前", position=0)
        ],
        records=[Record(source_row_number=2, data={"name": "A"})],
    )
    session.add(dataset)
    session.commit()
    dataset_id = dataset.id

    session.delete(dataset)
    session.commit()

    assert session.scalars(
        select(DatasetField).where(DatasetField.dataset_id == dataset_id)
    ).all() == []
    assert session.scalars(
        select(Record).where(Record.dataset_id == dataset_id)
    ).all() == []


def test_deleting_dataset_keeps_import_history(session: Session) -> None:
    dataset = Dataset(
        name="Example",
        source_filename="example.xlsx",
        sheet_name="Sheet1",
    )
    history = ImportHistory(
        source_filename="example.xlsx",
        sheet_name="Sheet1",
        status="succeeded",
        success_count=1,
    )
    dataset.import_history.append(history)
    session.add(dataset)
    session.commit()
    history_id = history.id

    session.delete(dataset)
    session.commit()

    stored_history = session.get(ImportHistory, history_id)
    assert stored_history is not None
    assert stored_history.dataset_id is None
