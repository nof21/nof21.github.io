---
# Homepage. Blocks live in layouts/_partials/hbx/blocks/<block>/block.html
title: ''
summary: ''
date: 2022-10-24
type: landing

design:
  spacing: '0'

sections:
  - block: profile-hero
    id: about
    content:
      username: me
      department: Department of Mechanical Engineering, University of Birmingham
      statement: >-
        I research how manufacturing systems shape the energy transition, quantifying the
        materials, energy and emissions that flow through industrial processes and supply
        chains, and developing methods for resource-efficient, low-carbon industry.
      buttons:
        - text: Download CV
          url: uploads/resume.pdf
          icon: document-arrow-down
          primary: true
        - text: Get in touch
          url: mailto:n.o.faverobolson@bham.ac.uk
          icon: envelope
      # The three flows in the hero diagram, top to bottom. Names link to the themes below.
      node_label: Industry
      themes:
        - id: energy-transition
          name: Manufacturing the energy transition
        - id: decarbonisation
          name: Industrial decarbonisation
        - id: resource-efficiency
          name: Resource efficiency and physical limits

  - block: research-themes
    id: research
    content:
      title: Research
      themes:
        - id: energy-transition
          name: Manufacturing the energy transition
          description: >-
            The materials, processes and supply chains needed to build wind turbines, solar
            panels and other low-carbon infrastructure, and how their material and carbon
            intensity is changing.
          papers:
            - bolson-2026-wind-foundations
            - bolson-2026-solar-efficiency
        - id: decarbonisation
          name: Industrial decarbonisation
          description: >-
            Process-level models of industrial energy use that show where electrification and
            hydrogen can replace fossil fuels, and what that means for energy demand and emissions.
          papers:
            - bolson-2026-electrification
            - saffer-2025-energy-allocation
        - id: resource-efficiency
          name: Resource efficiency and physical limits
          description: >-
            Material and energy flow analysis, circularity and thermodynamic benchmarks that
            measure how far industrial processes are from their practical and theoretical limits.
          papers:
            - bolson-2026-circularity
            - bolson-2025-thermo-minimum

  - block: news-list
    id: news
    content:
      title: News
      count: 5

  - block: selected-publications
    id: papers
    content:
      title: Selected publications
      count: 6
---
