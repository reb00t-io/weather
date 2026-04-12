# Embedded AI Agent

## Overview
Embed an AI agent that can interact with the app:
- the user has a chat interface
- it can access the data
- it can change the UI
- it can change the entire code to improve itself

It is basically Lovable integrated into the app.

## Use cases
### UI
- a chat integrated in the app
- can ask questions about the app
  - how to use it etc. -> replacing docs
  - issues -> access to code
- can ask to do things
  - controls the app via DOM
  - can fill in things
  - can change the DOM ad hoc to adjust the look and feel
- one use case is a controlled browser

### Data access
- knows where data is stored
- can analyze data, including writing code
- can create user interfaces ad hoc

### Modifications
This is a new way of building apps: the app makes changes to itself instead of a human making changes:
- via access to the DOM and the console it can verify changes
- it monitors errors to fix them if needed
- usually it makes changes via git
- can also make changes to itself for testing:
  - for html templates and trivial changes can be done in-place
  - an auto-revert if change is not confirmed as working
  - for app code this via a separate session (to avoid shutdown due to failure)
- can show the code if the user wants to see: we don't need IDEs anymore, just a code view with selection is fine, if at all

## Template
The template prepares the core of the above such that it is easy to continue from there.
Ideally, the app can be developed after bootstrapping just from within the app

### UI
- a chat widget
  - source code processing to generate docs
  - RAG and search over docs
- DOM access
  - tool calls to control the app, fill forms, etc
  - tools to modify the DOM ad hoc
- an optional browser view

### Data access
- pre-defined data store with tools and/or description on how to access it
- code execution
- templating for data analysis views

### Modifications
- claude code integration for full changes
- templating with live reload and auto revert for ad-hoc changes
