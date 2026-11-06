// insurance-docai: resource-group-scoped infrastructure.
// Deploy with infra/up.sh. Resources are added phase by phase (roadmap in CLAUDE.md).
targetScope = 'resourceGroup'

@description('Deployment environment. Only dev is deployed for this project; qa/prod are defined in infra/params.')
@allowed(['dev', 'qa', 'prod'])
param environmentName string = 'dev'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Log Analytics retention (days) and daily ingestion cap (GB, as text)')
param logRetentionDays int = 30
param logDailyCapGb string = '0.1'

@description('Entra object ID of the operator (gets data-plane roles; no keys are used)')
param operatorObjectId string

@description('Document Intelligence pricing tier: F0 = free (500 pages/mo, first 2 pages per doc), S0 = paid')
@allowed(['F0', 'S0'])
param docIntelSku string = 'S0'   // S0 since 2026-11-06 (augmentation experiment needs > 500 pages/month; ~$10 per 1,000 layout pages)

@description('Tags applied to every resource (mirrors shared/TAGGING.md)')
param tags object = {
  project: 'insurance-docai'
  environment: environmentName
  owner: 'williemaize'
  'cost-center': 'personal'
  'managed-by': 'bicep'
  repository: 'github.com/godot107/insurance-docai'
}

var suffix = take(uniqueString(resourceGroup().id), 6)

// ---------- Storage: private letters + (later) synthetic training set ----------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stdocai${suffix}'
  location: location
  tags: union(tags, { component: 'storage' })
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false   // no anonymous access, ever
    allowSharedKeyAccess: false    // Entra ID (RBAC) only, no account keys
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource rawPrivate 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'raw-private'
  properties: { publicAccess: 'None' }
}

resource curated 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'curated'   // pipeline output tables (Parquet) + run metadata; private (real-household numbers)
  properties: { publicAccess: 'None' }
}

resource synthetic 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'synthetic'
  properties: { publicAccess: 'None' }
}

// ---------- Document Intelligence ----------
resource docIntel 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'di-docai-${suffix}'
  location: location
  tags: union(tags, { component: 'docintel' })
  kind: 'FormRecognizer'
  sku: { name: docIntelSku }
  identity: { type: 'SystemAssigned' }   // used later to read training data from storage
  properties: {
    customSubDomainName: 'di-docai-${suffix}'   // required for Entra ID auth
    disableLocalAuth: true                       // no API keys: Entra ID only
    publicNetworkAccess: 'Enabled'
  }
}

// ---------- AI Language: PII detection/redaction before anything leaves private/ ----------
resource language 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'lang-docai-${suffix}'
  location: location
  tags: union(tags, { component: 'pii' })
  kind: 'TextAnalytics'
  sku: { name: 'F0' }   // free: 5,000 text records/month
  properties: {
    customSubDomainName: 'lang-docai-${suffix}'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

// ---------- Foundry (AI Services) + model: synthetic-letter wording now, decision assistant later ----------
@description('Chat model deployed in Foundry (pay per token; nothing is charged while idle)')
param chatModel string = 'gpt-4.1-mini'
param chatModelVersion string = '2025-04-14'
@description('Deployment capacity in thousands of tokens per minute')
param chatCapacityK int = 20

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: 'ai-docai-${suffix}'
  location: location
  tags: union(tags, { component: 'foundry' })
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: 'ai-docai-${suffix}'
    disableLocalAuth: true          // Entra ID only
    allowProjectManagement: true    // enables Foundry projects (agents, evaluations) on this resource
    publicNetworkAccess: 'Enabled'
  }
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: 'insurance-docai'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'insurance-docai'
    description: 'Synthetic letters + (later) premium decision assistant'
  }
}

resource chat 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: chatModel
  dependsOn: [ foundryProject ]   // the account allows one operation at a time
  sku: { name: 'GlobalStandard', capacity: chatCapacityK }
  properties: {
    model: { format: 'OpenAI', name: chatModel, version: chatModelVersion }
  }
}

// ---------- Monitoring: Log Analytics + diagnostic settings ----------
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-docai-${suffix}'
  location: location
  tags: union(tags, { component: 'monitoring' })
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: logRetentionDays
    workspaceCapping: { dailyQuotaGb: json(logDailyCapGb) }   // hard cap (dev ≈ cents/day)
  }
}

// Application Insights (workspace-based): usage telemetry from the Power Apps review app (sessions, screen views,
// Trace() events per decision) lands in the same Log Analytics workspace (tables AppEvents, AppPageViews).
// Local auth stays enabled: Power Apps sends telemetry with the connection string (it can't use Entra ID here);
// the connection string only allows *writing* telemetry, not reading it.
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-docai-${suffix}'
  location: location
  kind: 'web'
  tags: union(tags, { component: 'monitoring' })
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    IngestionMode: 'LogAnalytics'
    DisableIpMasking: false        // client IPs are masked (only coarse location is kept)
    RetentionInDays: 30
  }
}

resource diDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: docIntel
  name: 'to-log-analytics'
  properties: {
    workspaceId: logs.id
    logs: [ { categoryGroup: 'allLogs', enabled: true } ]
    metrics: [ { category: 'AllMetrics', enabled: true } ]
  }
}

resource foundryDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: foundry
  name: 'to-log-analytics'
  properties: {
    workspaceId: logs.id
    logs: [ { categoryGroup: 'allLogs', enabled: true } ]
    metrics: [ { category: 'AllMetrics', enabled: true } ]
  }
}

// ---------- Azure Databricks: lakehouse (Delta medallion) + MLflow ----------
@description('premium (Unity Catalog needs it). The 14-day trial SKU is refused on Azure Free Trial subscriptions.')
param databricksSku string = 'premium'
@description('Off: Azure Free Trial subscriptions can\'t attach a workspace to a Databricks account (no Unity Catalog; workspace deleted 2026-11-06). Turn on after upgrading to pay-as-you-go. Until then the lakehouse runs on Databricks Free Edition.')
param deployDatabricks bool = false

resource databricks 'Microsoft.Databricks/workspaces@2024-05-01' = if (deployDatabricks) {
  name: 'dbw-docai-${suffix}'
  location: location
  tags: union(tags, { component: 'databricks' })
  sku: { name: databricksSku }
  properties: {
    managedResourceGroupId: subscriptionResourceId('Microsoft.Resources/resourceGroups', 'databricks-rg-insurance-docai-${suffix}')
    parameters: {
      // Secure cluster connectivity without VNet injection adds a NAT gateway (~$32/month even idle).
      // Dev choice: clusters get public IPs (billed only while running); access is still Entra-only.
      enableNoPublicIp: { value: false }
    }
  }
}

// ---------- Azure SQL Database (free offer): serving layer for Power BI ----------
@description('Entra login name shown as the SQL admin (the operator)')
param sqlAdminLogin string = 'operator'
@description('SQL region: East US, East US 2, South Central US, West US 2, North Central US restrict new SQL servers for this trial subscription (2026-11-06); check Microsoft.Sql/locations/<loc>/capabilities status=Available')
param sqlLocation string = 'centralus'
@description('Public IPs allowed through the SQL firewall (operator workstation)')
param sqlClientIps array = []

resource sql 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: 'sql-docai-${suffix}-cus'
  location: sqlLocation
  tags: union(tags, { component: 'sql' })
  properties: {
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    administrators: {
      administratorType: 'ActiveDirectory'
      azureADOnlyAuthentication: true      // no SQL passwords at all
      login: sqlAdminLogin
      sid: operatorObjectId
      tenantId: subscription().tenantId
      principalType: 'User'
    }
  }
}

resource sqlDb 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sql
  name: 'docai'
  location: sqlLocation
  tags: union(tags, { component: 'sql' })
  sku: { name: 'GP_S_Gen5_2', tier: 'GeneralPurpose', family: 'Gen5', capacity: 2 }
  properties: {
    useFreeLimit: true                         // free offer: 100k vCore-seconds + 32 GB per month
    freeLimitExhaustionBehavior: 'AutoPause'   // pause instead of billing when the free amount runs out
    autoPauseDelay: 60
    minCapacity: json('0.5')
    maxSizeBytes: 34359738368
    zoneRedundant: false
    requestedBackupStorageRedundancy: 'Local'
  }
}

// Power BI service refreshes from Azure-hosted IPs; 0.0.0.0 is Azure's "allow Azure services" rule.
resource sqlAzure 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sql
  name: 'AllowAllWindowsAzureIps'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource sqlClients 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = [for (ip, i) in sqlClientIps: {
  parent: sql
  name: 'client-${i}'
  properties: { startIpAddress: ip, endIpAddress: ip }
}]

// ---------- Role assignments (data plane) ----------
var roles = {
  storageBlobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  storageBlobDataReader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  cognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908'
  cognitiveServicesOpenAIUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  azureAIUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
}

resource opLanguage 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: language
  name: guid(language.id, operatorObjectId, roles.cognitiveServicesUser)
  properties: {
    principalId: operatorObjectId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesUser)
  }
}

resource opFoundryOpenAI 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, operatorObjectId, roles.cognitiveServicesOpenAIUser)
  properties: {
    principalId: operatorObjectId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesOpenAIUser)
  }
}

resource opFoundryAI 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, operatorObjectId, roles.azureAIUser)
  properties: {
    principalId: operatorObjectId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.azureAIUser)
  }
}

resource opBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, operatorObjectId, roles.storageBlobDataContributor)
  properties: {
    principalId: operatorObjectId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataContributor)
  }
}

resource opDocIntel 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: docIntel
  name: guid(docIntel.id, operatorObjectId, roles.cognitiveServicesUser)
  properties: {
    principalId: operatorObjectId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesUser)
  }
}

// Document Intelligence's own identity can read blobs (custom-model training in phase 2)
resource diBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, docIntel.id, roles.storageBlobDataReader)
  properties: {
    principalId: docIntel.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataReader)
  }
}

output storageAccount string = storage.name
output blobEndpoint string = storage.properties.primaryEndpoints.blob
output docIntelEndpoint string = docIntel.properties.endpoint
output openaiEndpoint string = 'https://${foundry.properties.customSubDomainName}.openai.azure.com/'
output chatDeployment string = chat.name
output logWorkspace string = logs.name
output languageEndpoint string = language.properties.endpoint
output databricksUrl string = deployDatabricks ? 'https://${databricks!.properties.workspaceUrl}' : ''
output sqlServer string = sql.properties.fullyQualifiedDomainName
output sqlDatabase string = sqlDb.name
output logWorkspaceId string = logs.properties.customerId
output appInsightsConnectionString string = appInsights.properties.ConnectionString
