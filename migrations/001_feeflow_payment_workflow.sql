-- =====================================================================
-- FeeFlow v2.1 – One-tap payment declaration + administrator verification
-- Target: Supabase PostgreSQL (15+). Idempotent where practical.
--
-- If your existing FeeFlow schema already has tenants / students /
-- installments / receipts / reminder tables, keep yours and apply only the
-- NEW objects (marked [NEW]) plus the ALTERs in section 2.
-- Money is stored as integer paise (bigint).
-- =====================================================================
begin;

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------
-- 1. Core tables (create only if FeeFlow does not already have them)
-- ---------------------------------------------------------------------
create table if not exists tenants (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  short_code varchar(16) not null unique,
  logo_url text,
  contact_phone varchar(20),
  contact_email varchar(200),
  upi_vpa varchar(100) not null,
  upi_payee_name varchar(100) not null,
  created_at timestamp not null default (now() at time zone 'utc')
);

create table if not exists admin_users (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id),
  email varchar(200) not null unique,
  name varchar(200) not null,
  password_hash varchar(300) not null,
  active boolean not null default true,
  created_at timestamp not null default (now() at time zone 'utc')
);
create index if not exists ix_admin_users_tenant on admin_users(tenant_id);

create table if not exists students (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id),
  student_code varchar(40) not null,
  name varchar(200) not null,
  parent_name varchar(200),
  parent_mobile varchar(20) not null,
  unique (tenant_id, student_code),
  unique (tenant_id, id)
);

create table if not exists installments (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id),
  student_id uuid not null,
  description varchar(200) not null,
  billing_month char(7) not null,
  total_paise bigint not null,
  paid_paise bigint not null default 0,
  due_date date not null,
  status varchar(20) not null default 'UNPAID',
  created_at timestamp not null default (now() at time zone 'utc'),
  updated_at timestamp not null default (now() at time zone 'utc'),
  unique (tenant_id, id),
  foreign key (tenant_id, student_id) references students(tenant_id, id)
);

create table if not exists receipts (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id),
  installment_id uuid not null,
  receipt_number varchar(40) not null,
  amount_paise bigint not null,
  issued_at timestamp not null default (now() at time zone 'utc'),
  unique (tenant_id, receipt_number),
  foreign key (tenant_id, installment_id) references installments(tenant_id, id)
);

create table if not exists reminder_schedules (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id),
  installment_id uuid not null,
  scheduled_for timestamp not null,
  amount_paise bigint not null,
  status varchar(20) not null default 'SCHEDULED',
  updated_at timestamp not null default (now() at time zone 'utc'),
  foreign key (tenant_id, installment_id) references installments(tenant_id, id)
);

create table if not exists devices (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id),
  name varchar(100) not null,
  token_hash char(64) not null unique,
  active boolean not null default true,
  last_seen_at timestamp
);

create table if not exists sms_outbox (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id),
  to_mobile varchar(20) not null,
  body text not null,
  kind varchar(40) not null,
  status varchar(20) not null default 'QUEUED',
  created_at timestamp not null default (now() at time zone 'utc')
);

-- ---------------------------------------------------------------------
-- 2. Changes to existing tables
-- ---------------------------------------------------------------------
alter table tenants add column if not exists verification_grace_hours int not null default 24;
alter table tenants add column if not exists reminder_frequency_hours int not null default 72;
alter table tenants add column if not exists ack_sms_enabled boolean not null default true;

-- Installment = financial balance only (declaration status lives elsewhere).
alter table installments add column if not exists excess_paise bigint not null default 0;
alter table installments add column if not exists reminders_paused_until timestamp;
alter table installments drop constraint if exists ck_installment_amounts;
alter table installments add constraint ck_installment_amounts
  check (total_paise > 0 and paid_paise >= 0 and paid_paise <= total_paise and excess_paise >= 0);
alter table installments drop constraint if exists ck_installment_status;
alter table installments add constraint ck_installment_status
  check (status in ('UNPAID', 'PARTIALLY_PAID', 'PAID'));

alter table receipts add column if not exists declaration_id uuid;
alter table receipts add column if not exists public_token varchar(64);
alter table receipts add column if not exists total_paid_after_paise bigint;
alter table receipts add column if not exists balance_after_paise bigint;
alter table receipts add column if not exists issued_by uuid references admin_users(id);
create unique index if not exists uq_receipts_public_token on receipts(public_token);

alter table reminder_schedules add column if not exists to_mobile varchar(20);
alter table reminder_schedules add column if not exists body text;
alter table reminder_schedules add column if not exists cancel_reason varchar(200);
alter table reminder_schedules add column if not exists version int not null default 1;
alter table reminder_schedules add column if not exists sync_status varchar(20) not null default 'PENDING_SYNC';
alter table reminder_schedules add column if not exists synced_version int not null default 0;
alter table reminder_schedules add column if not exists sent_at timestamp;
create index if not exists ix_reminders_tenant_sync on reminder_schedules(tenant_id, sync_status);

alter table sms_outbox add column if not exists dedupe_key varchar(150);
alter table sms_outbox add column if not exists installment_id uuid;
alter table sms_outbox add column if not exists declaration_id uuid;
alter table sms_outbox add column if not exists reminder_id uuid;
alter table sms_outbox add column if not exists device_id uuid;
alter table sms_outbox add column if not exists error text;
alter table sms_outbox add column if not exists dispatched_at timestamp;
alter table sms_outbox add column if not exists sent_at timestamp;
create unique index if not exists uq_sms_outbox_dedupe on sms_outbox(dedupe_key);  -- no duplicate acknowledgements
create index if not exists ix_sms_outbox_tenant_status on sms_outbox(tenant_id, status);

-- Legacy: the old flow required a parent UTR. It is now optional and admin-side only.
do $$
begin
  if exists (select 1 from information_schema.columns where table_name = 'payments' and column_name = 'utr') then
    execute 'alter table payments alter column utr drop not null';
  end if;
end $$;

-- ---------------------------------------------------------------------
-- 3. [NEW] Secure payment links (only the SHA-256 of the token is stored)
-- ---------------------------------------------------------------------
create table if not exists payment_links (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id),
  installment_id uuid not null,
  token_hash char(64) not null unique,
  expires_at timestamp not null,
  revoked_at timestamp,
  created_at timestamp not null default (now() at time zone 'utc'),
  unique (tenant_id, id),
  foreign key (tenant_id, installment_id) references installments(tenant_id, id)
);
create index if not exists ix_payment_links_installment on payment_links(installment_id);

-- ---------------------------------------------------------------------
-- 4. [NEW] Payment declarations – what the parent SAID. Not a financial record.
-- ---------------------------------------------------------------------
create table if not exists payment_declarations (
  id uuid primary key default gen_random_uuid(),
  public_ref varchar(32) not null unique,
  tenant_id uuid not null references tenants(id),
  installment_id uuid not null,
  payment_link_id uuid not null,
  parent_identifier varchar(100),
  declared_amount_paise bigint not null check (declared_amount_paise > 0),
  payment_type varchar(10) not null default 'FULL' check (payment_type in ('FULL', 'PARTIAL')),
  payment_mode varchar(20) not null default 'UPI',
  upi_app varchar(30) check (upi_app is null or upi_app in ('GPAY', 'PHONEPE', 'PAYTM', 'OTHER_UPI', 'QR')),
  declaration_status varchar(30) not null check (declaration_status in (
    'PAYMENT_INITIATED', 'PAYMENT_PROCESSING', 'PAYMENT_REPORTED', 'PENDING_VERIFICATION',
    'VERIFIED', 'REJECTED', 'FAILED', 'EXPIRED')),
  declared_at timestamp,
  approximate_payment_time varchar(50),
  clarification_note text,
  parent_upi_reference varchar(64),          -- optional, never required from parents
  clarification_request text,
  clarification_requested_at timestamp,
  admin_note text,
  verified_payment_id uuid,
  reviewed_by uuid references admin_users(id),
  reviewed_at timestamp,
  rejection_reason text,
  grace_expires_at timestamp,
  grace_expiry_notified_at timestamp,
  idempotency_key varchar(100) not null,
  created_at timestamp not null default (now() at time zone 'utc'),
  updated_at timestamp not null default (now() at time zone 'utc'),
  constraint uq_declaration_idempotency unique (tenant_id, idempotency_key),
  constraint fk_decl_installment foreign key (tenant_id, installment_id) references installments(tenant_id, id),
  constraint fk_decl_link foreign key (tenant_id, payment_link_id) references payment_links(tenant_id, id),
  constraint ck_verified_has_payment check (declaration_status <> 'VERIFIED' or verified_payment_id is not null),
  constraint ck_rejected_has_reason check (declaration_status <> 'REJECTED' or rejection_reason is not null),
  unique (tenant_id, id)
);
-- Multiple taps can never create multiple ACTIVE declarations for one installment.
create unique index if not exists uq_one_active_declaration_per_installment
  on payment_declarations(installment_id)
  where declaration_status in ('PAYMENT_INITIATED', 'PAYMENT_PROCESSING', 'PAYMENT_REPORTED', 'PENDING_VERIFICATION');
create index if not exists ix_declarations_tenant_status on payment_declarations(tenant_id, declaration_status);
create index if not exists ix_declarations_grace on payment_declarations(grace_expires_at)
  where grace_expiry_notified_at is null;

-- ---------------------------------------------------------------------
-- 5. [NEW] Bank transactions (authorized statement import / feeds only)
-- ---------------------------------------------------------------------
create table if not exists bank_transactions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id),
  source varchar(40) not null check (source in ('STATEMENT_IMPORT', 'BANK_FEED', 'PROVIDER_API')),
  bank_reference varchar(64) not null,
  amount_paise bigint not null check (amount_paise > 0),
  credited_at timestamp not null,
  credited_to_vpa varchar(100),
  payer_vpa varchar(100),
  narration text,
  imported_by uuid,
  imported_at timestamp not null default (now() at time zone 'utc'),
  unique (tenant_id, source, bank_reference)
);

-- ---------------------------------------------------------------------
-- 6. [NEW] Verified payments – real money, confirmed by an administrator
-- ---------------------------------------------------------------------
create table if not exists verified_payments (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id),
  installment_id uuid not null,
  declaration_id uuid,
  bank_transaction_id uuid references bank_transactions(id),
  amount_paise bigint not null check (amount_paise > 0),
  applied_paise bigint not null check (applied_paise >= 0),
  excess_paise bigint not null default 0 check (excess_paise >= 0),
  bank_reference varchar(64),
  paid_at timestamp,
  payment_mode varchar(20) not null default 'UPI',
  evidence_note text,
  reconciliation_source varchar(30) not null default 'ADMIN_MANUAL',
  verified_by uuid references admin_users(id),
  verified_at timestamp not null default (now() at time zone 'utc'),
  receipt_id uuid references receipts(id),
  constraint ck_verified_split check (applied_paise + excess_paise = amount_paise),
  constraint ck_verified_evidence check (bank_reference is not null or evidence_note is not null or bank_transaction_id is not null),
  constraint uq_verified_bank_reference unique (tenant_id, bank_reference),     -- never credit a transaction twice
  constraint uq_verified_bank_transaction unique (bank_transaction_id),
  foreign key (tenant_id, installment_id) references installments(tenant_id, id),
  foreign key (tenant_id, declaration_id) references payment_declarations(tenant_id, id)
);
create index if not exists ix_verified_payments_installment on verified_payments(installment_id);

alter table payment_declarations drop constraint if exists fk_decl_verified_payment;
alter table payment_declarations add constraint fk_decl_verified_payment
  foreign key (verified_payment_id) references verified_payments(id) deferrable initially deferred;

-- ---------------------------------------------------------------------
-- 7. [NEW] Excess / duplicate payment adjustments
-- ---------------------------------------------------------------------
create table if not exists payment_adjustments (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id),
  installment_id uuid not null,
  verified_payment_id uuid not null references verified_payments(id),
  amount_paise bigint not null check (amount_paise > 0),
  kind varchar(20) not null default 'EXCESS',
  status varchar(20) not null default 'FLAGGED' check (status in ('FLAGGED', 'REFUNDED', 'ADJUSTED')),
  note text,
  resolved_by uuid references admin_users(id),
  resolved_at timestamp,
  created_at timestamp not null default (now() at time zone 'utc'),
  foreign key (tenant_id, installment_id) references installments(tenant_id, id)
);

-- ---------------------------------------------------------------------
-- 8. [NEW] Notifications, audit log, idempotency
-- ---------------------------------------------------------------------
create table if not exists admin_notifications (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id),
  kind varchar(40) not null,
  message text not null,
  declaration_id uuid,
  installment_id uuid,
  created_at timestamp not null default (now() at time zone 'utc'),
  read_at timestamp
);
create index if not exists ix_admin_notifications_tenant on admin_notifications(tenant_id, read_at);

create table if not exists audit_logs (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id),
  actor_type varchar(20) not null check (actor_type in ('PARENT', 'ADMIN', 'SYSTEM', 'DEVICE')),
  actor_id varchar(100),
  action varchar(60) not null,
  declaration_id uuid,
  installment_id uuid,
  previous_status varchar(30),
  new_status varchar(30),
  reason text,
  source varchar(40) not null,
  details_json text,
  created_at timestamp not null default (now() at time zone 'utc')
);
create index if not exists ix_audit_tenant_time on audit_logs(tenant_id, created_at desc);
create index if not exists ix_audit_declaration on audit_logs(declaration_id);
create index if not exists ix_audit_installment on audit_logs(installment_id);

-- Financial records must not be silently overwritten: audit log is append-only,
-- verified payments cannot be deleted or have their money fields changed.
create or replace function feeflow_block_mutation() returns trigger language plpgsql as $$
begin
  raise exception '% on % is not allowed (append-only financial record)', tg_op, tg_table_name;
end $$;

drop trigger if exists trg_audit_logs_append_only on audit_logs;
create trigger trg_audit_logs_append_only before update or delete on audit_logs
  for each row execute function feeflow_block_mutation();

create or replace function feeflow_protect_verified_payment() returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'verified payments cannot be deleted; record a reversing adjustment instead';
  end if;
  if new.amount_paise <> old.amount_paise or new.applied_paise <> old.applied_paise
     or new.excess_paise <> old.excess_paise or new.installment_id <> old.installment_id
     or coalesce(new.bank_reference, '') <> coalesce(old.bank_reference, '') then
    raise exception 'money fields of a verified payment are immutable';
  end if;
  return new;
end $$;

drop trigger if exists trg_verified_payments_protect on verified_payments;
create trigger trg_verified_payments_protect before update or delete on verified_payments
  for each row execute function feeflow_protect_verified_payment();

create table if not exists idempotency_records (
  id uuid primary key default gen_random_uuid(),
  scope varchar(120) not null,
  key varchar(100) not null,
  request_hash char(64) not null,
  status_code int not null,
  response_json text not null,
  created_at timestamp not null default (now() at time zone 'utc'),
  unique (scope, key)
);

-- ---------------------------------------------------------------------
-- 9. Row Level Security (defence in depth).
-- The FastAPI backend connects with the service role and enforces tenant
-- isolation itself; RLS stops any direct Supabase client access across tenants.
-- Expects a custom JWT claim `tenant_id`.
-- ---------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array['payment_links', 'payment_declarations', 'verified_payments', 'payment_adjustments',
                           'bank_transactions', 'admin_notifications', 'audit_logs', 'sms_outbox', 'reminder_schedules']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists tenant_isolation on %I', t);
    -- Supabase provides auth.jwt(); on plain Postgres RLS stays enabled with no policy
    -- (deny-all for non-owner roles; the backend connects as the table owner).
    if exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
               where n.nspname = 'auth' and p.proname = 'jwt') then
      execute format(
        'create policy tenant_isolation on %I for select using (tenant_id = nullif(auth.jwt() ->> ''tenant_id'', '''')::uuid)', t);
    end if;
  end loop;
end $$;

alter table idempotency_records enable row level security;  -- backend (service role) only

commit;
