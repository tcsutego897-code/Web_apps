[build]
builder = "NIXPACKS"

[deploy]
buildCommand = "npm run build"
startCommand = "npm start"
healthcheckPath = "/"
restartPolicyType = "ON_FAILURE"