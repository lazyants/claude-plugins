-- db-guardrails: SQL Server layer 1. Render ONLY with the adjacent Python
-- installer; its placeholders are escaped Unicode string literals, not sqlcmd
-- variables. The installer uses -b and -x, and sends this batch on stdin.
-- Requires an administrator with visibility of server/database principals.
-- Both passwords are reset on success. The entire batch is transactional.
-- The runtime role gets DML on one existing schema, including future tables.
-- The migrator gets db_owner in this database, so protect its credentials.
-- This checks direct DDL privileges; review triggers, signed/EXECUTE AS code,
-- ownership chains and later grants separately. DELETE remains a DML right.
SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @App sysname = __APP_USER__, @Migrator sysname = __MIGRATOR_USER__,
        @RuntimeRole sysname = __RUNTIME_ROLE__, @Schema sysname = __SCHEMA__,
        @AppPassword nvarchar(128) = __APP_PASSWORD__,
        @MigratorPassword nvarchar(128) = __MIGRATOR_PASSWORD__,
        @AppId int, @RoleId int, @SchemaId int = SCHEMA_ID(__SCHEMA__),
        @Sql nvarchar(max), @Impersonated bit = 0;
DECLARE @LoginTargets TABLE (name sysname NOT NULL);
DECLARE @UserTargets TABLE (name sysname NOT NULL);

BEGIN TRY
    BEGIN TRANSACTION;
    IF IS_SRVROLEMEMBER(N'sysadmin') <> 1
        THROW 51000, 'Installer requires a sysadmin SQL Server login.', 1;
    IF @SchemaId IS NULL
        THROW 51001, 'Application schema must already exist.', 1;

    -- Existing app accounts must already be ordinary SQL logins. Refuse
    -- server roles, direct server grants, database ownership or other mapped
    -- identities instead of silently promising separation around them.
    IF EXISTS (SELECT 1 FROM sys.server_principals WHERE name IN (@App, @Migrator) AND type <> 'S')
        THROW 51002, 'App and migrator must be dedicated SQL logins.', 1;
    IF EXISTS (SELECT 1 FROM sys.server_role_members WHERE member_principal_id = SUSER_ID(@App))
        THROW 51003, 'App login has a server role; remove elevation before installation.', 1;
    IF EXISTS (SELECT 1 FROM sys.server_permissions WHERE grantee_principal_id = SUSER_ID(@App)
               AND NOT (permission_name = N'CONNECT SQL' AND state = 'G'))
        THROW 51004, 'App login has direct server privileges; review them before installation.', 1;
    IF EXISTS (SELECT 1 FROM sys.databases WHERE owner_sid = SUSER_SID(@App))
        THROW 51005, 'App login owns a database; transfer ownership before installation.', 1;
    IF EXISTS (SELECT 1 FROM sys.server_principals WHERE owning_principal_id = SUSER_ID(@App))
       OR EXISTS (SELECT 1 FROM sys.endpoints WHERE principal_id = SUSER_ID(@App))
        THROW 51006, 'App login owns a server securable; transfer ownership first.', 1;

    SET @AppId = USER_ID(@App);
    SET @RoleId = USER_ID(@RuntimeRole);
    IF EXISTS (SELECT 1 FROM sys.database_principals WHERE name IN (@App, @Migrator)
               AND (type <> 'S' OR authentication_type <> 1 OR SUSER_SID(name) IS NULL OR sid <> SUSER_SID(name)))
        THROW 51007, 'Existing app/migrator users must map to their same-name SQL logins.', 1;
    IF @RoleId IS NOT NULL AND EXISTS (SELECT 1 FROM sys.database_principals WHERE principal_id = @RoleId AND type <> 'R')
        THROW 51008, 'Runtime role name is occupied by another principal.', 1;
    IF EXISTS (SELECT 1 FROM sys.database_role_members WHERE member_principal_id = @AppId AND (@RoleId IS NULL OR role_principal_id <> @RoleId))
        THROW 51009, 'App user has another database role; review/remove it before installation.', 1;
    IF EXISTS (SELECT 1 FROM sys.database_role_members WHERE member_principal_id = @RoleId)
       OR EXISTS (SELECT 1 FROM sys.database_role_members WHERE role_principal_id = @RoleId AND (@AppId IS NULL OR member_principal_id <> @AppId))
        THROW 51010, 'Existing runtime role is nested or shared; choose a dedicated role.', 1;
    IF EXISTS (SELECT 1 FROM sys.schemas WHERE principal_id IN (@AppId, @RoleId))
       OR EXISTS (SELECT 1 FROM sys.objects WHERE principal_id IN (@AppId, @RoleId))
       OR EXISTS (SELECT 1 FROM sys.database_principals WHERE owning_principal_id IN (@AppId, @RoleId))
        THROW 51011, 'App user/runtime role owns a securable; transfer ownership first.', 1;
    IF EXISTS (SELECT 1 FROM sys.database_permissions WHERE grantee_principal_id = @AppId
               AND NOT ((permission_name = N'CONNECT' AND class = 0 AND state = 'G')
                 OR (permission_name IN (N'SELECT',N'INSERT',N'UPDATE',N'DELETE') AND state = 'G')))
        THROW 51012, 'App user has additional direct privileges; review/remove them first.', 1;
    IF EXISTS (SELECT 1 FROM sys.database_permissions WHERE grantee_principal_id = @RoleId
               AND NOT (class = 3 AND major_id = @SchemaId
                 AND ((permission_name IN (N'SELECT',N'INSERT',N'UPDATE',N'DELETE') AND state = 'G')
                   OR (permission_name IN (N'ALTER',N'TAKE OWNERSHIP') AND state = 'D'))))
        THROW 51013, 'Existing runtime role has unexpected privileges; review them first.', 1;

    IF SUSER_ID(@App) IS NULL
    BEGIN
        SET @Sql = N'CREATE LOGIN ' + QUOTENAME(@App) + N' WITH PASSWORD = '
                 + QUOTENAME(@AppPassword, '''') + N', CHECK_POLICY = ON;';
        EXEC sys.sp_executesql @Sql;
    END;
    IF SUSER_ID(@Migrator) IS NULL
    BEGIN
        SET @Sql = N'CREATE LOGIN ' + QUOTENAME(@Migrator) + N' WITH PASSWORD = '
                 + QUOTENAME(@MigratorPassword, '''') + N', CHECK_POLICY = ON;';
        EXEC sys.sp_executesql @Sql;
    END;
    SET @Sql = N'ALTER LOGIN ' + QUOTENAME(@App) + N' WITH PASSWORD = ' + QUOTENAME(@AppPassword, '''')
             + N'; ALTER LOGIN ' + QUOTENAME(@App) + N' ENABLE; ALTER LOGIN ' + QUOTENAME(@Migrator)
             + N' WITH PASSWORD = ' + QUOTENAME(@MigratorPassword, '''')
             + N'; ALTER LOGIN ' + QUOTENAME(@Migrator) + N' ENABLE;';
    EXEC sys.sp_executesql @Sql;
    IF @AppId IS NULL
    BEGIN
        SET @Sql = N'CREATE USER ' + QUOTENAME(@App) + N' FOR LOGIN ' + QUOTENAME(@App) + N';';
        EXEC sys.sp_executesql @Sql;
    END;
    IF USER_ID(@Migrator) IS NULL
    BEGIN
        SET @Sql = N'CREATE USER ' + QUOTENAME(@Migrator) + N' FOR LOGIN ' + QUOTENAME(@Migrator) + N';';
        EXEC sys.sp_executesql @Sql;
    END;
    IF @RoleId IS NULL
    BEGIN
        SET @Sql = N'CREATE ROLE ' + QUOTENAME(@RuntimeRole) + N' AUTHORIZATION dbo;';
        EXEC sys.sp_executesql @Sql;
    END;
    SET @Sql = N'GRANT CONNECT TO ' + QUOTENAME(@App) + N'; GRANT CONNECT TO ' + QUOTENAME(@Migrator)
             + N'; GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::' + QUOTENAME(@Schema)
             + N' TO ' + QUOTENAME(@RuntimeRole)
             + N'; DENY ALTER, TAKE OWNERSHIP ON SCHEMA::' + QUOTENAME(@Schema) + N' TO ' + QUOTENAME(@RuntimeRole) + N';';
    EXEC sys.sp_executesql @Sql;
    IF NOT EXISTS (SELECT 1 FROM sys.database_role_members WHERE role_principal_id = USER_ID(@RuntimeRole) AND member_principal_id = USER_ID(@App))
    BEGIN
        SET @Sql = N'ALTER ROLE ' + QUOTENAME(@RuntimeRole) + N' ADD MEMBER ' + QUOTENAME(@App) + N';';
        EXEC sys.sp_executesql @Sql;
    END;
    IF NOT EXISTS (SELECT 1 FROM sys.database_role_members WHERE role_principal_id = USER_ID(N'db_owner') AND member_principal_id = USER_ID(@Migrator))
    BEGIN
        SET @Sql = N'ALTER ROLE db_owner ADD MEMBER ' + QUOTENAME(@Migrator) + N';';
        EXEC sys.sp_executesql @Sql;
    END;

    -- Capture all identity targets with administrator metadata visibility.
    -- Under app impersonation, catalog visibility can hide a more privileged
    -- login/user; a PUBLIC grant on that specific identity must still count.
    INSERT @LoginTargets (name)
      SELECT name FROM sys.server_principals WHERE type IN ('S','U','G','E','X') AND name <> @App;
    INSERT @UserTargets (name)
      SELECT name FROM sys.database_principals WHERE type IN ('S','U','G','E','X') AND name <> @App;

    -- Impersonate the actual login, so inherited public permissions count.
    EXECUTE AS LOGIN = __APP_USER__;
    SET @Impersonated = 1;
    IF IS_SRVROLEMEMBER(N'sysadmin') <> 0
       OR HAS_PERMS_BY_NAME(NULL, N'SERVER', N'CONTROL SERVER') <> 0
       OR HAS_PERMS_BY_NAME(NULL, N'SERVER', N'ALTER ANY LOGIN') <> 0
       OR HAS_PERMS_BY_NAME(NULL, N'SERVER', N'ALTER ANY SERVER ROLE') <> 0
       OR HAS_PERMS_BY_NAME(NULL, N'SERVER', N'IMPERSONATE ANY LOGIN') <> 0
       OR HAS_PERMS_BY_NAME(NULL, N'SERVER', N'ALTER ANY DATABASE') <> 0
       OR HAS_PERMS_BY_NAME(NULL, N'SERVER', N'CREATE ANY DATABASE') <> 0
        THROW 51014, 'App login retains effective server elevation.', 1;
    IF EXISTS (SELECT 1 FROM @LoginTargets WHERE HAS_PERMS_BY_NAME(name,N'LOGIN',N'IMPERSONATE') = 1)
       OR EXISTS (SELECT 1 FROM @UserTargets WHERE HAS_PERMS_BY_NAME(name,N'USER',N'IMPERSONATE') = 1)
        THROW 51019, 'App login can impersonate another login or database user.', 1;
    IF HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'CONTROL') <> 0
       OR HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'ALTER') <> 0
       OR HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'CREATE TABLE') <> 0
       OR HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'CREATE SCHEMA') <> 0
       OR HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'ALTER ANY SCHEMA') <> 0
       OR HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'ALTER ANY USER') <> 0
       OR HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'ALTER ANY ROLE') <> 0
       OR HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'IMPERSONATE ANY USER') <> 0
       OR HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'EXECUTE') <> 0
        THROW 51015, 'App login retains effective database DDL/impersonation/execute privileges.', 1;
    IF EXISTS (SELECT 1 FROM sys.schemas WHERE name NOT IN (N'sys',N'INFORMATION_SCHEMA')
               AND (HAS_PERMS_BY_NAME(name,N'SCHEMA',N'ALTER') <> 0
                 OR HAS_PERMS_BY_NAME(name,N'SCHEMA',N'CONTROL') <> 0
                 OR HAS_PERMS_BY_NAME(name,N'SCHEMA',N'TAKE OWNERSHIP') <> 0
                 OR HAS_PERMS_BY_NAME(name,N'SCHEMA',N'EXECUTE') <> 0))
       OR EXISTS (SELECT 1 FROM sys.objects WHERE is_ms_shipped = 0
               AND (HAS_PERMS_BY_NAME(QUOTENAME(SCHEMA_NAME(schema_id)) + N'.' + QUOTENAME(name),N'OBJECT',N'ALTER') <> 0
                 OR HAS_PERMS_BY_NAME(QUOTENAME(SCHEMA_NAME(schema_id)) + N'.' + QUOTENAME(name),N'OBJECT',N'CONTROL') <> 0
                 OR HAS_PERMS_BY_NAME(QUOTENAME(SCHEMA_NAME(schema_id)) + N'.' + QUOTENAME(name),N'OBJECT',N'TAKE OWNERSHIP') <> 0
                 OR HAS_PERMS_BY_NAME(QUOTENAME(SCHEMA_NAME(schema_id)) + N'.' + QUOTENAME(name),N'OBJECT',N'EXECUTE') <> 0))
        THROW 51016, 'App login retains effective schema/object DDL or executable code privileges.', 1;
    IF HAS_PERMS_BY_NAME(@Schema,N'SCHEMA',N'SELECT') <> 1
       OR HAS_PERMS_BY_NAME(@Schema,N'SCHEMA',N'INSERT') <> 1
       OR HAS_PERMS_BY_NAME(@Schema,N'SCHEMA',N'UPDATE') <> 1
       OR HAS_PERMS_BY_NAME(@Schema,N'SCHEMA',N'DELETE') <> 1
        THROW 51017, 'App schema DML privileges could not be verified.', 1;
    REVERT;
    SET @Impersonated = 0;

    EXECUTE AS LOGIN = __MIGRATOR_USER__;
    SET @Impersonated = 1;
    IF IS_ROLEMEMBER(N'db_owner') <> 1
       OR HAS_PERMS_BY_NAME(DB_NAME(),N'DATABASE',N'CONTROL') <> 1
       OR HAS_PERMS_BY_NAME(DB_NAME(),N'DATABASE',N'CREATE TABLE') <> 1
       OR HAS_PERMS_BY_NAME(@Schema,N'SCHEMA',N'ALTER') <> 1
       OR EXISTS (SELECT 1 FROM sys.objects WHERE is_ms_shipped = 0 AND type = 'U' AND schema_id = @SchemaId
                   AND HAS_PERMS_BY_NAME(QUOTENAME(@Schema) + N'.' + QUOTENAME(name),N'OBJECT',N'ALTER') <> 1)
        THROW 51018, 'Migrator retains restrictions that prevent schema migrations.', 1;
    REVERT;
    SET @Impersonated = 0;
    COMMIT TRANSACTION;
    PRINT N'DB_GUARDRAILS_SQLSERVER_OK';
END TRY
BEGIN CATCH
    IF @Impersonated = 1 REVERT;
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
