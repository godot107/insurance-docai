// dev environment. Deploy: infra/up.sh dev   (only dev is deployed for this project; qa/prod are ready, not built)
using '../main.bicep'

param environmentName = 'dev'
param docIntelSku = 'S0'
param logRetentionDays = 30
param logDailyCapGb = '0.1'
param sqlLocation = 'centralus'      // trial subscriptions: several US regions refuse new SQL servers
param deployDatabricks = false       // Azure Databricks needs pay-as-you-go (Unity Catalog); see docs/ENVIRONMENTS.md

// Identity- and machine-specific values come from the deploying shell or CI job (never committed):
param operatorObjectId = readEnvironmentVariable('OPERATOR_OBJECT_ID')
param sqlAdminLogin = readEnvironmentVariable('SQL_ADMIN_LOGIN', 'operator')
param sqlClientIps = json(readEnvironmentVariable('SQL_CLIENT_IPS', '[]'))
