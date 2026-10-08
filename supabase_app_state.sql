-- Sthetcar IA V33 Online - estado centralizado
-- Execute este script no SQL Editor do projeto Supabase.

create table if not exists public.app_state (
    key text primary key,
    data jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default timezone('utc', now())
);

alter table public.app_state enable row level security;

-- Sem polÃ­ticas para anon/authenticated: o estado fica acessÃ­vel somente pelo backend
-- que usa a SUPABASE_SECRET_KEY.
