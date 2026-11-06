// Subscription-level guardrails (deploy once per subscription with infra/governance.sh).
// Azure Policy is free for Azure resources.
//  - Every resource inherits the standard tags from its resource group if it lacks them
//    (shared/TAGGING.md keys), so hand-made or tool-made resources still show up in cost reports by project.
targetScope = 'subscription'

param location string = 'eastus'
param tagNames array = ['project', 'environment', 'owner', 'cost-center', 'managed-by', 'repository']

var inheritIfMissing = '/providers/Microsoft.Authorization/policyDefinitions/ea3f2387-9b95-492a-a190-fcdc54f7b070'
var tagContributor = '4a9ae827-6dc8-4573-8ac7-8239d42aa03f'

resource inherit 'Microsoft.Authorization/policyAssignments@2024-04-01' = [for t in tagNames: {
  name: 'inherit-tag-${t}'
  location: location   // required because the assignment has a managed identity
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Inherit tag "${t}" from resource group if missing'
    policyDefinitionId: inheritIfMissing
    parameters: { tagName: { value: t } }
    nonComplianceMessages: [ { message: 'Resource is missing the "${t}" tag; it will be copied from its resource group.' } ]
  }
}]

// The modify effect needs permission to write tags.
resource canTag 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (t, i) in tagNames: {
  name: guid(subscription().id, 'inherit-tag-${t}', tagContributor)
  properties: {
    principalId: inherit[i].identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', tagContributor)
  }
}]
