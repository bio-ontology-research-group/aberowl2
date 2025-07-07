console.log('Loading ontology.js');

document.addEventListener('alpine:init', () => {
    
// Create an Alpine.js store for sharing data between components
Alpine.store('ontologyApp', {
  ontology: {},
  selectedClass: null,
  selectedProp: null,
  currentTab: 'Overview'
});

Alpine.data('ontologyApp', () => ({
  ontology: {
    classes: [],
    properties: [],
    name: 'Loading...',
    acronym: '',
    submission: {
      description: '',
      version: '',
      date_released: '',
      home_page: '',
      documentation: '',
      publication: '',
      has_ontology_language: 'OWL',
      nb_classes: 'N/A',
      nb_properties: 'N/A',
      nb_object_properties: 'N/A',
      nb_data_properties: 'N/A',
      nb_annotation_properties: 'N/A',
      nb_individuals: 'N/A',
      max_children: 'N/A',
      avg_children: 'N/A',
      max_depth: 'N/A',
      dl_expressivity: 'N/A',
      axiom_count: 'N/A',
      logical_axiom_count: 'N/A',
      declaration_axiom_count: 'N/A'
    }
  },
  classesMap: new Map(),
  propsMap: new Map(),
  tabs: [
      'Overview', 'Browse', 'DLQuery', 'SPARQL', 'LLMQuery', 'Download'
  ],
  currentTab: 'Overview',
  selectedClass: null,
  selectedProp: null,
  dlQuery: null,
  dlQueryExp: null,
  dlResults: [],
  rawSparqlResults: null,
  simResults: [],
  search: '',
  searchResults: [],
  searchResultsShow: false,
  format: 'text/html',
  query: '',
  llmQuery: '',
  detectedParams: null,
  isLoading: false,
    
  init() {
    
    // Fetch the ontology data
    this.fetchOntologyData();
        
    window.addEventListener('hash:changed', () => {
      this.checkUrlHash();
    });

    window.addEventListener('hashchange', () => {
      this.checkUrlHash();
    });
  
    // Initialize with default SPARQL query
    this.setQueryClassesExampleQuery();
      
  },
    
  renderNode(node, level) {
    const isActive = this.isNodeActive(node);
    const isCollapsed = node.collapsed || false;
    const hasChildren = node.children && node.children.length > 0;
    let html = `
      <li class="${isActive ? 'active' : ''}"> 
        <span @click.prevent="toggleCollapsed('${node.owlClass}')">
            <i class="glyphicon ${isCollapsed ? 'glyphicon-plus' : 'glyphicon-minus'}"></i>
        </span>
        <a href="#" @click.prevent="handleNodeClick(null, '${node.owlClass}')">${node.label}</a>
      </li>
    `;
    if (hasChildren && !isCollapsed) {
      html += `<ul>`;
      for (let child of node.children) {
        // Recursively render child nodes
        html += this.renderNode(child, level + 1);
      }
      html += `</ul>`;
    }
    return html;
  }, 

  renderProperty(node, level) {
    const isActive = this.isPropertyActive(node);
    const isCollapsed = node.collapsed || false;
    const hasChildren = node.children && node.children.length > 0;
    let html = `
      <li class="${isActive ? 'active' : ''}"> 
        <span @click.prevent="toggleProperty('${node.owlClass}')">
            <i class="glyphicon ${isCollapsed ? 'glyphicon-plus' : 'glyphicon-minus'}"></i>
        </span>
        <a href="#" @click.prevent="handlePropertyClick(null, '${node.owlClass}')">${node.label}</a>
      </li>
    `;
    if (hasChildren && !isCollapsed) {
      html += `<ul>`;
      for (let child of node.children) {
        // Recursively render child nodes
        html += this.renderProperty(child, level + 1);
      }
      html += `</ul>`;
    }
    return html;
  }, 
  toggleProperty(owlClass) {
    const node = this.propsMap.get(owlClass); 
    if (!node) return;
    // Toggle the collapsed state
    node.collapsed = !node.collapsed;
  },

  toggleCollapsed(owlClass) {
    const node = this.classesMap.get(owlClass); 
    if (!node) return;
    // Toggle the collapsed state
    node.collapsed = !node.collapsed;
  },

  // Helper method to ensure all classes and their children are collapsed
  ensureCollapsedState(classes) {
    if (!classes) return;
    
    const processNode = (node) => {
      if (!node) return;
      
      // Set node to collapsed by default
      node.collapsed = true;
      
      // Process children recursively
      if (node.children && node.children.length > 0) {
        for (let i = 0; i < node.children.length; i++) {
          processNode(node.children[i]);
        }
      }
    };
    
    // Process all top-level classes
    for (let i = 0; i < classes.length; i++) {
      processNode(classes[i]);
    }
    // console.log('All classes and children have been collapsed');
  },
  
  fetchOntologyData() {
    this.isLoading = true;

    // Fetch ontology metadata
    const sparqlQuery = `
        PREFIX owl: <http://www.w3.org/2002/07/owl#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX dc: <http://purl.org/dc/elements/1.1/>
        PREFIX dcterms: <http://purl.org/dc/terms/>
        PREFIX vann: <http://purl.org/vocab/vann/>
        SELECT ?p ?o
        WHERE {
          ?s a owl:Ontology .
          ?s ?p ?o .
          FILTER(isLiteral(?o))
        }
    `;
    const sparqlUrl = `/api/api/sparql.groovy?query=${encodeURIComponent(sparqlQuery)}`;
    fetch(sparqlUrl, { headers: { 'Accept': 'application/sparql-results+json' } })
        .then(response => response.json())
        .then(data => {
            const metadata = {};
            data.results.bindings.forEach(binding => {
                const prop = binding.p.value.split('#').pop().split('/').pop();
                const value = binding.o.value;
                if (!metadata[prop]) {
                    metadata[prop] = [];
                }
                metadata[prop].push(value);
            });

            const first = (arr) => arr && arr.length > 0 ? arr[0] : undefined;

            this.ontology.name = first(metadata.title) || 'Ontology';
            this.ontology.acronym = first(metadata.preferredNamespacePrefix) || '';
            this.ontology.submission.description = first(metadata.description) || first(metadata.comment) || '';
            this.ontology.submission.version = first(metadata.versionInfo) || '';
            this.ontology.submission.date_released = first(metadata.date) || '';
            this.ontology.submission.home_page = first(metadata.homepage) || '';
        })
        .catch(error => {
            console.error('Error fetching ontology metadata:', error);
        });

    // Fetch ontology statistics from the new endpoint
    fetch('/api/api/getStatistics.groovy')
        .then(response => response.json())
        .then(stats => {
            this.ontology.submission.nb_classes = stats.class_count ?? 'N/A';
            this.ontology.submission.nb_properties = stats.property_count ?? 'N/A';
            this.ontology.submission.nb_object_properties = stats.object_property_count ?? 'N/A';
            this.ontology.submission.nb_data_properties = stats.data_property_count ?? 'N/A';
            this.ontology.submission.nb_annotation_properties = stats.annotation_property_count ?? 'N/A';
            this.ontology.submission.nb_individuals = stats.individual_count ?? 'N/A';
            this.ontology.submission.axiom_count = stats.axiom_count ?? 'N/A';
            this.ontology.submission.logical_axiom_count = stats.logical_axiom_count ?? 'N/A';
            this.ontology.submission.declaration_axiom_count = stats.declaration_axiom_count ?? 'N/A';
            this.ontology.submission.dl_expressivity = stats.dl_expressivity ?? 'N/A';
        })
        .catch(error => {
            console.error('Error fetching ontology statistics:', error);
            // Optionally set all stats to N/A on error
            this.ontology.submission.nb_classes = 'N/A';
            this.ontology.submission.nb_properties = 'N/A';
            this.ontology.submission.nb_object_properties = 'N/A';
            this.ontology.submission.nb_data_properties = 'N/A';
            this.ontology.submission.nb_annotation_properties = 'N/A';
            this.ontology.submission.nb_individuals = 'N/A';
            this.ontology.submission.axiom_count = 'N/A';
            this.ontology.submission.logical_axiom_count = 'N/A';
            this.ontology.submission.declaration_axiom_count = 'N/A';
            this.ontology.submission.dl_expressivity = 'N/A';
        });
    
    // Fetch the ontology classes and properties from the backend
    fetch('/api/api/runQuery.groovy?type=subclass&direct=true&query=<http://www.w3.org/2002/07/owl%23Thing>')
      .then(response => {
        if (!response.ok) {
          throw new Error('Network response was not ok');
        }
        return response.json();
      })
      .then(data => {
        console.log('Fetched ontology data:', data);
        // 1. Index classes and re-initialize children arrays
        this.ontology.classes = data.result || [];
        if (this.ontology.classes.length > 0 && this.ontology.name === 'Ontology') {
          this.ontology.name = this.ontology.classes[0].ontology;
        }
        this.ensureCollapsedState(this.ontology.classes);
        for (let i = 0; i < this.ontology.classes.length; i++) {
          this.classesMap.set(this.ontology.classes[i].owlClass, this.ontology.classes[i]);
        }
        this.isLoading = false;
      })
      .catch(error => {
        console.error('Error fetching ontology data:', error);
        this.isLoading = false;
        window.dispatchEvent(new CustomEvent('ontology:error', { 
          detail: { message: 'Failed to load ontology data: ' + error.message }
        }));
      });
    // Fetch the ontology properties
    fetch('/api/api/getObjectProperties.groovy')
      .then(response => {
        if (!response.ok) {
          throw new Error('Network response was not ok');
        }
        return response.json();
      })
      .then(data => {
        console.log('Fetched properties:', data);
        // 1. Index properties and re-initialize children arrays
        this.ontology.properties = data.result || [];
        // Build the properties map
        for (let i = 0; i < this.ontology.properties.length; i++) {
          this.propsMap.set(this.ontology.properties[i].owlClass, this.ontology.properties[i]);
        }
        // Ensure all properties are collapsed by default
        this.ensureCollapsedState(this.ontology.properties);
        this.isLoading = false;
      })
      .catch(error => {
        console.error('Error fetching ontology data:', error);
        this.isLoading = false;
        window.dispatchEvent(new CustomEvent('ontology:error', { 
          detail: { message: 'Failed to load ontology data: ' + error.message }
        }));
      });
  },
  
  // These methods are no longer needed as we're loading real data

  checkUrlHash() {
    // Parse the URL hash to determine navigation
    const hash = window.location.hash.substring(1); // Remove the # character
    if (!hash) return;
    
    const parts = hash.split('/');
    if (parts.length > 0) {
      const tab = parts[1];
      if (this.tabs.includes(tab)) {
        this.currentTab = tab;
      }
      
      if (parts.length > 2 && parts[2]) {
        const owlClass = decodeURIComponent(parts[2]);
        this.handleClassNavigation(tab, owlClass, parts[3]);
      }
    }
  },
  
  handleClassNavigation(tab, owlClass, query) {
    if (tab === 'Browse' && owlClass) {
      if (this.classesMap.has(owlClass)) {
        const obj = this.classesMap.get(owlClass);
        this.selectedClass = obj;
        
        // Expand the selected class to show its children
        if (obj.children && obj.children.length > 0) {
          obj.collapsed = false;
          // Process all children recursively
          this.processChildrenRecursively(obj.children);
        } else {
          // If no children are loaded yet, load them
          this.loadChildrenForClass(owlClass);
        }
        
        // Execute a DL query to get subclasses
        this.executeBrowseDLQuery(owlClass);
      } else {
        // Make a real API call to fetch the class hierarchy
        this.isLoading = true;
        
        // Fetch the class hierarchy from the backend
        fetch(`/api/api/findRoot.groovy?query=${encodeURIComponent(owlClass)}`)
          .then(response => {
            if (!response.ok) {
              throw new Error('Network response was not ok');
            }
            return response.json();
          })
          .then(data => {
            // Process the class hierarchy and find the root
            this.findRoot(owlClass, data);
            
            // Set the current tab to Browse
            this.currentTab = 'Browse';
            
            // Execute a DL query to get subclasses
            this.executeBrowseDLQuery(owlClass);
            
            this.isLoading = false;
          })
          .catch(error => {
            console.error('Error fetching class hierarchy:', error);
            this.isLoading = false;
          });
      }
    } else if (tab === 'DLQuery' && owlClass) {
	if (this.classesMap.has(owlClass)) {
	    //replace spaces by underscores
        this.selectedClass = this.classesMap.get(owlClass)
      }
      
      if (query) {
          this.dlQuery = query;
          this.executeDLQuery(owlClass, query);
      }
    } else if (tab === 'Property' && owlClass) {
      if (this.propsMap.has(owlClass)) {
        this.selectedProp = this.propsMap.get(owlClass);
      }
    }
  },


    getFormattedClass(owlClass) {
        if (!owlClass || typeof owlClass !== 'string') {
            return owlClass;
        }

        const manchesterKeywords = new Set(['and', 'or', 'not', 'some', 'only', 'value', 'min', 'max', 'exactly', 'that', 'inverse', 'self']);

        // Get all labels from classesMap and propsMap
        const allLabels = new Map();
        for (const classObj of this.classesMap.values()) {
            if (classObj.label) {
                allLabels.set(classObj.label.toLowerCase(), classObj.label);
            }
        }
        for (const propObj of this.propsMap.values()) {
            if (propObj.label) {
                allLabels.set(propObj.label.toLowerCase(), propObj.label);
            }
        }

        // Sort labels by length, descending, to match longer labels first
        const sortedLabels = Array.from(allLabels.keys()).sort((a, b) => b.length - a.length);

        let processedQuery = owlClass;

        for (const label of sortedLabels) {
            if (manchesterKeywords.has(label)) {
                continue;
            }

            // Case-insensitive replacement of whole words
            const regex = new RegExp(`\\b${label.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&')}\\b`, 'gi');
            
            const originalLabel = allLabels.get(label);
            let replacement = originalLabel;
            if (originalLabel.includes(' ')) {
                replacement = `'${originalLabel}'`;
            }
            
            processedQuery = processedQuery.replace(regex, replacement);
        }

        return processedQuery;
    },
    
  executeDLQuery(owlClass, queryType, labels = true) {
      this.isLoading = true;

      formattedQuery = this.getFormattedClass(owlClass);
      // Check if the query is a class label and format it properly for the Manchester OWL Syntax parser
      
      console.log('Executing DL query for class:', formattedQuery, 'with type:', queryType, 'and labels:', labels);
    // Make a real API call to the backend
    fetch(`/api/api/runQuery.groovy?query=${encodeURIComponent(formattedQuery)}&type=${queryType}&labels=${labels}`)
      .then(response => {
        if (!response.ok) {
          throw new Error('Network response was not ok');
        }
        return response.json();
      })
      .then(data => {
        this.dlResults = data.result || [];
        this.isLoading = false;
      })
      .catch(error => {
        console.error('Error executing DL query:', error);
        this.isLoading = false;
        // Fallback to empty results
        this.dlResults = [];
      });
  },

  findRoot(owlClass, data) {
    // Process the class hierarchy and find the root
    this.ontology.classes = data.result;
    
    // Clear and rebuild the classesMap and parentDict
    this.classesMap = new Map();
    const parentDict = new Map();
    this.ontology.parentDict = parentDict;
    
    // Process all classes recursively to ensure they're in the classesMap
    const processNodesRecursively = (nodes) => {
      if (!nodes || nodes.length === 0) return;
      
      for (let i = 0; i < nodes.length; i++) {
        const node = nodes[i];
        if (!node) continue;
        
        // Add to classesMap
        this.classesMap.set(node.owlClass, node);
        
        // Set collapsed state
        node.collapsed = true;
        
        // Process children recursively and build parent dictionary
        if (node.children && node.children.length > 0) {
          node.children.forEach(child => {
            if (child && child.owlClass) {
              parentDict.set(child.owlClass, node.owlClass);
            }
          });
          processNodesRecursively(node.children);
        }
      }
    };
    
    // Process the entire hierarchy
    processNodesRecursively(this.ontology.classes);
    
    // Set the selected class
    this.selectedClass = this.classesMap.get(owlClass);
    
    // If we found the selected class, expand it
    if (this.selectedClass) {
      this.selectedClass.collapsed = false;
      
      // Also expand all parent nodes to make the selected class visible
      this.expandParentNodes(owlClass);
    }
    
    // Update the store
    Alpine.store('ontologyApp').selectedClass = this.selectedClass;
    
    return {
      classesMap: this.classesMap,
      selectedClass: this.selectedClass,
      ontology: this.ontology
    };
  },
  
  // Helper method to expand all parent nodes of a given class
  expandParentNodes(owlClass) {
    // Use the parent dictionary to directly find the path to the root
    const parentDict = this.ontology.parentDict;
    if (!parentDict) return;
    
    // Build the path from the class to the root
    const path = [];
    let currentClass = owlClass;
    
    while (currentClass && parentDict.has(currentClass)) {
      const parentIri = parentDict.get(currentClass);
      const parentNode = this.classesMap.get(parentIri);
      
      if (parentNode) {
        path.push(parentNode);
        currentClass = parentIri;
      } else {
        break;
      }
    }
    
    // Expand all nodes in the path (from root to target)
    path.reverse().forEach(node => {
      node.collapsed = false;
    });
  },

  setTab(tab) {
    this.currentTab = tab;
    // Update store
    Alpine.store('ontologyApp').currentTab = tab;
    // Update URL hash
    window.location.hash = `/${tab}`;
  },
  
  isTabActive(tab) {
    return this.currentTab === tab || (tab === 'Browse' && this.currentTab === 'Property');
  },

  formatTopics(topics) {
    if (!topics) return '';
    return topics.map(topic => 
      `<span class="label label-default aberowl-topic">${topic}</span>`
    ).join(' ');
  },
  
  formatList(list) {
    if (!list) return '';
    return list.join(', ');
  },

  getOverviewMetadata() {
    if (!this.ontology || !this.ontology.submission) return [];
    
    const submission = this.ontology.submission;
    return [
      ['Description', submission.description],
      ['Version', submission.version],
      ['Release date', submission.date_released],
      // ['Homepage', `<a href="${submission.home_page}" target="_blank">${submission.home_page}</a>`],
      // ['Documentation', `<a href="${submission.documentation}" target="_blank">${submission.documentation}</a>`],
      // ['Publication', submission.publication],
      ['Ontology language', submission.has_ontology_language],
      // ['License', 'CC-BY 4.0'],
      // ['Authors', 'The Pizza Ontology Working Group'],
      // ['Contact', '<a href="mailto:pizza@example.org">pizza@example.org</a>']
    ];
  },

  // This will be handled with x-html in the template

  getPropertyFields() {
    const obj = this.selectedProp;
    if (!obj) return [];
    
    const ignoreFields = new Set([
      'collapsed', 'children', 'deprecated', 'owlClass'
    ]);
    
    const allFields = Object.keys(obj)
      .filter(item => !ignoreFields.has(item));
    const allFieldsSet = new Set(allFields);
    
    let fields = [
      'identifier', 'label', 'definition', 'class', 'ontology'
    ];
    const fieldSet = new Set(fields);
    
    fields = fields.filter(item => allFieldsSet.has(item));
    
    for (let item of allFieldsSet) {
      if (!fieldSet.has(item)) {
        fields.push(item);
      }
    }
    
    return fields;
  },
  
  getPropertyData() {
    if (!this.selectedProp) return [];
    
    const obj = this.selectedProp;
    const htmlFields = new Set(['SubClassOf', 'Equivalent', 'Disjoint']);
    const fields = this.getPropertyFields();
    
    return fields.map(item => {
      let value = obj[item];
      
      if (htmlFields.has(item)) {
        // Will be handled with x-html in the template
        return [item, value.toString()];
      }
      
      if (value && Array.isArray(value)) {
        value = value.join(', ');
      }
      
      return [item, value];
    });
  },

  getClassFields() {
    const obj = this.selectedClass;
    if (!obj) return [];
    
    const ignoreFields = new Set([
      'collapsed', 'children', 'deprecated', 'owlClass'
    ]);
    
    const allFields = Object.keys(obj)
      .filter(item => !ignoreFields.has(item));
    const allFieldsSet = new Set(allFields);
    
    let fields = [
      'identifier', 'label', 'definition', 'class', 'ontology',
      'Equivalent', 'SubClassOf', 'Disjoint'
    ];
    const fieldSet = new Set(fields);
    
    fields = fields.filter(item => allFieldsSet.has(item));
    
    for (let item of allFieldsSet) {
      if (!fieldSet.has(item)) {
        fields.push(item);
      }
    }
    
    return fields;
  },
  
  getClassData() {
    if (!this.selectedClass) return [];
    
    const obj = this.selectedClass;
    const htmlFields = new Set(['SubClassOf', 'Equivalent', 'Disjoint']);
    const fields = this.getClassFields();
    
    // Skip fields that are already displayed separately
    const skipFields = new Set(['label', 'definition', 'owlClass']);
    
    return fields
      .filter(item => !skipFields.has(item))
      .map(item => {
        let value = obj[item];
        
        if (htmlFields.has(item)) {
          // Will be handled with x-html in the template
          return [item, value ? value.toString() : ''];
        }
        
        if (value && Array.isArray(value)) {
          value = value.join(', ');
        }
        
        return [item, value];
      });
  },

  getDLQueryButtons() {
    return [
      ['subclass', 'Subclasses'],
      ['subeq', 'Sub and Equivalent'],
      ['equivalent', 'Equivalent'],
      ['superclass', 'Superclasses'],
      ['supeq', 'Super and Equivalent']
    ];
  },
  
  isDLQueryActive(queryType) {
    return this.dlQuery === queryType;
  },
  
  setDLQuery(owlClass, queryType) {
    this.dlQuery = queryType;
    
    // Store the original query for display purposes
    this.dlQueryExp = owlClass;
    
    // Update URL hash
    window.location.hash = `/DLQuery/${encodeURIComponent(owlClass)}/${queryType}`;
    
    // Execute the query with the potentially formatted class
    this.executeDLQuery(owlClass, queryType);
  },
  
  // Execute DL query when a class is selected in Browse view
  executeBrowseDLQuery(owlClass, queryType = 'subclass') {
    this.dlQuery = queryType;
    this.executeDLQuery(owlClass, queryType);
  },

  
  onDlQueryChange(event) {
    this.dlQueryExp = event.target.value;
    this.dlQuery = null;
  },

  getSparqlFormats() {
    return [
      {name: 'HTML',  format:'text/html'},
      {name: 'XML',  format:'application/sparql-results+xml'},
      {name: 'JSON',  format:'application/sparql-results+json'},
      // {name: 'Javascript',  format:'application/javascript'},
      // {name: 'Turtle',  format:'text/turtle'},
      // {name: 'RDF/XML',  format:'application/rdf+xml'},
      // {name: 'N-Triples',  format:'text/plain'},
      {name: 'CSV',  format:'text/csv'},
      {name: 'TSV',  format:'text/tab-separated-values'}
    ];
  },

  onSparqlChange(event) {
    this.query = event.target.value;
  },
  
  onFormatChange(event) {
    this.format = event.target.value;
  },

    setCheesyPizzaExampleQuery(event) {
  if (event) event.preventDefault();
    const query = "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> \n" +
    "PREFIX owl: <http://www.w3.org/2002/07/owl#> \n" +
    "SELECT DISTINCT ?class \n" +
    "WHERE { \n" +
    "VALUES ?class {OWL superclass <> <> { cheesy_pizza } } . } \n" +
    "ORDER BY ?class \n"
  this.query = query
    },
    
  setQueryClassesExampleQuery(event) {
    if (event) event.preventDefault();
const query = "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> \n" +
      "PREFIX owl: <http://www.w3.org/2002/07/owl#> \n" +
            "SELECT DISTINCT ?class \n" + 
      "WHERE { ?class rdf:type owl:Class . } \n" +
      "ORDER BY ?class \n" +
      "LIMIT 10";
    
    this.query = query;
  },
  
  setDDIEMFilterExampleQuery(event) {
    if (event) event.preventDefault();
const query = "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> \n" +
      "PREFIX owl: <http://www.w3.org/2002/07/owl#> \n" +
            "SELECT DISTINCT ?class \n" + 
      " WHERE { ?class rdf:type owl:Class . } \n" +
      "ORDER BY ?class \n" +
      "LIMIT 10";

    // const query2 = "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>      \n" +
    // "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>   \n" +
    // "PREFIX obo: <http://purl.obolibrary.org/obo/>   \n" +   
    // "SELECT ?procedure ?evidenceCode ?phenotypeCorrected   \n" +   
    // "FROM <http://ddiem.phenomebrowser.net>    \n" +  
    // "WHERE {    \n" +     
    // "	?procedure rdf:type ?procedureType .    \n" +  
    // "	?procedure obo:RO_0002558 ?evidenceCode .     \n" + 
    // "	?procedure obo:RO_0002212 ?phenotypes .      \n" +
    // "	?phenotypes rdfs:label ?phenotypeCorrected .      \n" +
    // "	FILTER ( ?procedureType in (    \n" +
    // "		OWL equivalent <http://ddiem.phenomebrowser.net/sparql> <DDIEM> {     \n" +   
    // "			'metabolite replacement'       \n" +
    // "		}        \n" +
    // "	) ).     \n" +
    // "}";
    
    this.query = query;
  },

    // Parse the raw JSON string into a structured object
    parseSparqlResults(rawJsonString) {
        try {
            return JSON.parse(rawJsonString);
        } catch (error) {
            console.error('Error parsing SPARQL results:', error);
            return {
                results: {
                    bindings: [{ error: { type: "literal", value: 'Error parsing results: ' + error.message } }]
                }
            };
        }
    },
    
    // Transform SPARQL results for display only
    getDisplayResults(results) {
        if (!results || !results.results || !results.results.bindings) {
            return [{label: 'No results'}];
        }
        
        return results.results.bindings.map(binding => {
            // Get the first variable in the binding
            const firstVarName = Object.keys(binding)[0];
            if (!firstVarName) {
                return { label: "No data" };
            }
            
            const value = binding[firstVarName].value;
            // If it's a URI, extract the last part after # or /
            let displayValue = value;
            if (binding[firstVarName].type === 'uri') {
                displayValue = value.includes('#')
                    ? value.split('#').pop()
                    : value.split('/').pop();
            }
            
            return {
                label: displayValue,
                fullUri: value
            };
        });
    },

    
    executeSparql(event) {
    if (event) event.preventDefault();
    this.isLoading = true;

    const sparqlUrl = '/api/runSparqlQuery.groovy';
    const formData = new URLSearchParams();
    formData.append('query', this.query.trim());
    formData.append('endpoint', this.endpoint);

    const queryUrl = `${sparqlUrl}?${formData.toString()}`;
    
    fetch(queryUrl, {
        method: 'GET',
        headers: {
      'Accept': 'application/sparql-results+json,*/*;q=0.9'
        }
    })
      .then(response => {
    if (!response.ok) {
        throw new Error('Network response was not ok');
    }
    return response.text(); // Get the raw response as text
      })
      .then(data => {
  // Store the original parsed results
  this.rawSparqlResults = this.parseSparqlResults(data);
  
  // Transform results for display
  this.dlResults = this.getDisplayResults(this.rawSparqlResults);

  this.isLoading = false;
      })
      .catch(error => {
  console.error('Error executing SPARQL query:', error);
  this.isLoading = false;
  this.rawSparqlResults = { results: { bindings: [{ error: { type: "literal", value: 'Error: ' + error.message } }] } };
  this.dlResults = [{label: 'Error: ' + error.message}];
      });
    },
    
  downloadResults(event) {
    if (event) event.preventDefault();
      if (!this.rawSparqlResults || !this.rawSparqlResults.results || !this.rawSparqlResults.results.bindings || this.rawSparqlResults.results.bindings.length === 0) {
        console.error('No results to download');
        return;
      }
      
      let content = '';
      let filename = 'sparql-results';
      let contentType = this.format;
      
      // Format the results based on the selected format
      switch (this.format) {
        case 'text/html':
          content = '<table border="1">\n<thead>\n<tr>\n';
          
          const vars = this.rawSparqlResults.head?.vars || [];
          vars.forEach(varName => {
            content += `<th>${varName}</th>\n`;
          });
          
          content += '</tr>\n</thead>\n<tbody>\n';
          
          // Add each result row
          this.rawSparqlResults.results.bindings.forEach(binding => {
            content += '<tr>\n';
            vars.forEach(varName => {
              const value = binding[varName]?.value || '';
              content += `<td>${value}</td>\n`;
            });
            content += '</tr>\n';
          });
          
          content += '</tbody>\n</table>';
          filename += '.html';
          break;
          
        case 'text/csv':
          // Create CSV content
          const csvVars = this.rawSparqlResults.head?.vars || [];
          content = csvVars.join(',') + '\n';
          
          this.rawSparqlResults.results.bindings.forEach(binding => {
            const values = csvVars.map(varName => {
              const value = binding[varName]?.value || '';
              // Escape quotes in CSV
              return `"${value.replace(/"/g, '""')}"`;
            });
            content += values.join(',') + '\n';
          });
          
          filename += '.csv';
          break;
          
        case 'text/tab-separated-values':
          // Create TSV content
          const tsvVars = this.rawSparqlResults.head?.vars || [];
          content = tsvVars.join('\t') + '\n';
          
          this.rawSparqlResults.results.bindings.forEach(binding => {
            const values = tsvVars.map(varName => {
              const value = binding[varName]?.value || '';
              // Replace tabs with spaces in TSV
              return value.replace(/\t/g, ' ');
            });
            content += values.join('\t') + '\n';
          });
          
          filename += '.tsv';
          break;
          
        case 'application/json':
        case 'application/sparql-results+json':
          // Use the original JSON results
          content = JSON.stringify(this.rawSparqlResults, null, 2);
          filename += '.json';
          break;
          
        case 'application/rdf+xml':
        case 'application/sparql-results+xml':
          // Create XML content
          content = '<?xml version="1.0" encoding="UTF-8"?>\n';
          content += '<sparql xmlns="http://www.w3.org/2005/sparql-results#">\n';
          
          // Add head section with variables
          content += '  <head>\n';
          const xmlVars = this.rawSparqlResults.head?.vars || [];
          xmlVars.forEach(varName => {
            content += `    <variable name="${varName}"/>\n`;
          });
          content += '  </head>\n';
          
          // Add results section
          content += '  <results>\n';
          
          this.rawSparqlResults.results.bindings.forEach(binding => {
            content += '    <result>\n';
            
            xmlVars.forEach(varName => {
              if (binding[varName]) {
                const type = binding[varName].type;
                const value = binding[varName].value;
                
                if (type === 'uri') {
                  content += `      <binding name="${varName}"><uri>${value}</uri></binding>\n`;
                } else {
                  content += `      <binding name="${varName}"><literal>${value}</literal></binding>\n`;
                }
              }
            });
            
            content += '    </result>\n';
          });
          
          content += '  </results>\n</sparql>';
          filename += '.xml';
          break;
          
        default:
          // Default to plain text
          content = this.dlResults.map(item => item.label).join('\n');
          
          filename += '.txt';
          contentType = 'text/plain';
      }
      
      // Create a blob with the content
      const blob = new Blob([content], { type: contentType });
      
      // Create a download link and trigger the download
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      
      // Clean up
      document.body.removeChild(link);
      URL.revokeObjectURL(link.href);
    },
    
  getDownloadFields() {
    return [
      'Version',
      'Release date',
      'Download'
    ];
  },


  setBioGatewayExampleQuery(event) {
    if (event) event.preventDefault();
    
    const query = "SELECT ?interacting_protein ?gene   \n" +
    "WHERE   \n" +
    "{   \n" +
    "    ?interacting_protein <http://purl.obolibrary.org/obo/RO_0002331> ?gene .   \n" +
    "    VALUES ?gene {    \n" +
    "          OWL equivalent <https://biogw-db.nt.ntnu.no:4333/sparql> <GO> {    \n" +
    "             'response to hypoxia'   \n" +
    "          }    \n" +
    "    } .  \n" +  
    "}";
    
    this.query = query;
  },
  
  // This will be handled with x-show directives in the template

  isNodeActive(node) {
    if (!node || !this.selectedClass) return false;
    
    // Handle both node objects and direct owlClass strings
    const nodeOwlClass = typeof node === 'string' ? node : node.owlClass;
    return this.selectedClass.owlClass === nodeOwlClass;
  },
  
  // Toggle the collapsed state of a node
  toggleCollapsed(event, node) {
    event.preventDefault();
    event.stopPropagation();
    
    if (!node) return;
    
    // Toggle collapsed state
    node.collapsed = !node.collapsed;
    
    // If expanding and node has children, make sure they're properly initialized
    if (!node.collapsed && node.children && node.children.length > 0) {
      // Keep children collapsed initially
      this.processChildrenRecursively(node.children);
    }
    
    // If expanding and node doesn't have children yet, load them
    if (!node.collapsed && (!node.children || node.children.length === 0)) {
      this.loadChildrenForClass(node.owlClass);
    }
  },
  
  handleNodeClick(event, owlClass) {
    if (event) event.preventDefault();
    
    const obj = this.classesMap.get(owlClass);
    if (!obj) {
      // If the class is not in the map, fetch it from the backend
      // this.isLoading = true;
      
      fetch(`/api/api/findRoot.groovy?query=${encodeURIComponent(owlClass)}`)
        .then(response => {
          if (!response.ok) {
            throw new Error('Network response was not ok');
          }
          return response.json();
        })
        .then(data => {
          // Process the class hierarchy and find the root
          const state = this.findRoot(owlClass, data);
          
          // Set the current tab to Browse
          this.currentTab = 'Browse';
          Alpine.store('ontologyApp').currentTab = 'Browse';
          
          // Execute a DL query to get subclasses
          this.executeBrowseDLQuery(owlClass);
          
          this.isLoading = false;
        })
        .catch(error => {
          console.error('Error fetching class hierarchy:', error);
          this.isLoading = false;
        });
      return;
    }
    
    // Update URL hash
    window.location.hash = `/Browse/${encodeURIComponent(owlClass)}`;
    
    // Set selected class
    this.selectedClass = obj;
    Alpine.store('ontologyApp').selectedClass = obj;
    
    this.currentTab = 'Browse';
    Alpine.store('ontologyApp').currentTab = 'Browse';
    
    // Toggle the collapsed state to show/hide children
    obj.collapsed = !obj.collapsed;
    
    // Process all children recursively
    if (!obj.collapsed && obj.children && obj.children.length > 0) {
      this.processChildrenRecursively(obj.children);
    }
    
    // If we need to load children from the backend
    if (!obj.collapsed && (!obj.children || obj.children.length === 0)) {
      this.loadChildrenForClass(owlClass);
    }
    
    // Execute a DL query to get subclasses when a node is selected
    this.executeBrowseDLQuery(owlClass);
    
    // Set DL query expression based on class label
    if (this.selectedClass) {
      // Use the class label directly without modification
      // Our improved executeDLQuery will handle the conversion
      this.dlQueryExp = this.selectedClass.label;
    }
  },
  
  // Load children for a class from the backend
  loadChildrenForClass(owlClass) {
    // this.isLoading = true;
    
    fetch(`/api/api/runQuery.groovy?direct=true&axioms=true&query=${encodeURIComponent(owlClass)}&type=subclass`)
      .then(response => {
        if (!response.ok) {
          throw new Error('Network response was not ok');
        }
        return response.json();
      })
      .then(data => {
        const obj = this.classesMap.get(owlClass);
        if (obj && data.result) {
          // Set children and ensure they're in the classesMap
          obj.children = data.result;
          console.log('Loaded children for class:', obj, obj.children);
          // Add each child to the classesMap
          for (let i = 0; i < obj.children.length; i++) {
            const child = obj.children[i];
            if (child && child.owlClass) {
              this.classesMap.set(child.owlClass, child);
              
              // Also update the parent dictionary
              if (this.ontology.parentDict) {
                this.ontology.parentDict.set(child.owlClass, owlClass);
              }
            }
          }
          
          // Process children recursively
          this.processChildrenRecursively(obj.children);
        }
        this.isLoading = false;
      })
      .catch(error => {
        console.error('Error loading children for class:', error);
        this.isLoading = false;
      });
  },
  
  // Recursively process all children to ensure they're properly initialized
  processChildrenRecursively(children) {
    if (!children || children.length === 0) return;
    
    for (let i = 0; i < children.length; i++) {
      const child = children[i];
      if (!child) continue;
      
      // Ensure child is in the classesMap
      if (!this.classesMap.has(child.owlClass)) {
        this.classesMap.set(child.owlClass, child);
      }
      
      // Set child to collapsed by default
      child.collapsed = true;
      
      // Process this child's children recursively
      if (child.children && child.children.length > 0) {
        this.processChildrenRecursively(child.children);
      }
    }
  },

  // This is handled by the checkUrlHash method and event listeners

  // Already implemented above


  isPropertyActive(node) {
    return this.selectedProp && this.selectedProp.owlClass === node.owlClass;
  },
  
  // Toggle the collapsed state of a property node
  togglePropertyCollapsed(event, node) {
    event.preventDefault();
    event.stopPropagation();
    
    if (!node) return;
    
    // Toggle collapsed state
    node.collapsed = !node.collapsed;
    
    // If expanding and node has children, make sure they're properly initialized
    if (!node.collapsed && node.children && node.children.length > 0) {
      // Process children recursively
      this.processChildrenRecursively(node.children);
    }
  },
  
  handlePropertyClick(event, owlClass) {
    if (event) event.preventDefault();
    
    const obj = this.propsMap.get(owlClass);
    if (!obj) return;
    
    // Update URL hash
    window.location.hash = `/Property/${encodeURIComponent(owlClass)}`;
    
    // Set selected property
    this.selectedProp = obj;
    Alpine.store('ontologyApp').selectedProp = obj;
    
    this.currentTab = 'Property';
    Alpine.store('ontologyApp').currentTab = 'Property';
    
    // Toggle the collapsed state to show/hide children
    obj.collapsed = !obj.collapsed;
    
    // Process all children recursively if expanding
    if (!obj.collapsed && obj.children && obj.children.length > 0) {
      this.processChildrenRecursively(obj.children);
    }
  },

  // Already implemented above

  handleSearchChange(event) {
    const value = event.target.value;
    this.search = value;
    if (value.length >= 3) {
      this.searchResultsShow = true;
      this.executeSearch(value);
    } else {
      this.searchResultsShow = false;
    }
  },
  
    executeSearch(search, callback = null) {
    // Get the port number from the URL
    const port = window.location.port;
    // Use the port number to construct the correct index name
    const indexName = `class_index_${port}`;
    
    // Direct Elasticsearch query for label matches
    fetch(`/elastic/${indexName}/_search`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        query: {
            bool: {
		should: [
		    {match: {label: {query: search.toLowerCase(), boost: 2}}},
		    {match_bool_prefix: {label: search.toLowerCase()}}
		]
            // must: [	    
              // {match_bool_prefix: {label: search.toLowerCase()}},
            // ]
          }
        },
        _source: {excludes: ['embedding_vector',]},
        size: 10
      })
    })
    .then(response => {
      if (!response.ok) throw new Error('Network response was not ok');
      return response.json();
    })
    .then(data => {
      console.log('Elasticsearch response:', data);
      this.searchResults = data.hits?.hits?.map(hit => ({
        owlClass: hit._source.owlClass,
        label: hit._source.label,
        score: hit._score
      })) || [];

	if (callback) {
	    callback(this.searchResults);
	}
    })
    .catch(error => {
      console.error('Elasticsearch error:', error);
	this.searchResults = [];
	if (callback) {
	    callback([]);
	}
    });
  },

  handleSearchItemClick(search) {
    this.search = search;
    this.searchResultsShow = false;
  },

	detectNLParams() {
	    console.log('Detecting parameters from natural language query:', this.llmQuery);
	    
	    // Default values in case the API call fails
	    let query = "";
	    let type = "";
	    const direct = "true";
	    const labels = "true";
	    const axioms = "false";
	    
	    // Return a promise to allow async processing
	    return new Promise((resolve, reject) => {
	        // Call the query parser API
	        fetch('/llm', {
	            method: 'POST',
	            headers: {
	                'Content-Type': 'application/json',
	            },
	            body: JSON.stringify({ input: this.llmQuery })
	        })
	        .then(response => {
	            if (!response.ok) {
	                throw new Error('Network response was not ok');
	            }
	            return response.json();
	        })
	        .then(data => {
	            console.log('Query parser response:', data);
	            
	            // The response should already be a parsed JSON object
	            // Extract query and type from the data
	            if (data && typeof data === 'object') {
	                if (data.query && data.query !== 'unknown') {
	                    query = data.query;
	                }
	                if (data.type && data.type !== 'unknown') {
	                    type = data.type;
	                }
	            }
	            
	            // Resolve with the parameters
	            resolve({
	                "query": query,
	                "type": type,
	                "direct": direct,
	                "labels": labels,
	                "axioms": axioms
	            });
	        })
	        .catch(error => {
	            console.error('Error calling query parser API:', error);
	            // Reject with error instead of using default values
	            reject(error);
	        });
	    });
	},


    processLLMQuery() {
    if (!this.llmQuery) {
        alert('Please enter a natural language query');
        return;
    }
    
    this.isLoading = true;
    const dlQueryUrl = "/api/api/runQuery.groovy";
    
    // First detect parameters from natural language
    this.detectNLParams()
        .then(all_params => {
            const query = all_params.query;
            const type = all_params.type;
            const direct = all_params.direct;
            const labels = all_params.labels;
            const axioms = all_params.axioms;
            
            // Display the detected parameters

            
            // Use callback to wait for search completion
            this.executeSearch(query, (searchResults) => {
                console.log("Elasticsearch query response:", searchResults);
                
                if (searchResults.length === 0) {
                    console.error("No search results found");
                    this.dlResults = [{label: "No search results found"}];
                    this.isLoading = false;
                    return;
                }
                
                const top_result = searchResults[0].owlClass;
                console.log("Top result from Elasticsearch:", top_result);
                const formattedQuery = encodeURIComponent(this.getFormattedClass(top_result));

		all_params.query = searchResults[0].label[0];
		this.detectedParams = JSON.stringify(all_params, null, 2);
                
                // Build the query URL with parameters
                const params = `query=${formattedQuery}&type=${type}&direct=${direct}&labels=${labels}&axioms=${axioms}`;
                const queryUrl = `${dlQueryUrl}?${params}`;

                // Execute the query with detected parameters
                fetch(queryUrl, {
                    method: 'GET',
                    headers: {
                        'Accept': 'application/json,*/*;q=0.9'
                    }
                })
                .then(response => {
                    if (!response.ok) {
                        throw new Error(`HTTP error! status: ${response.status}`);
                    }
                    return response.json();
                })
                .then(data => {
                    console.log("LLM query response:", data);
                    
                    // Clear any previous results
                    this.dlResults = [];
                    
                    // Process the results for display
                    if (data && data.result) {
                        // Format the results similar to SPARQL tab
                        this.dlResults = data.result.map(item => {
                            // If item is a string, use it directly
                            if (typeof item === 'string') {
                                return { label: item };
                            }
                            
                            // If item is an object with owlClass, use that
                            if (item.owlClass) {
                                const displayValue = item.label ?
                                    item.label :
                                    item.owlClass.includes('#') ?
                                        item.owlClass.split('#').pop() :
                                        item.owlClass.split('/').pop();
                                        
                                return {
                                    label: `<a href="#/Browse/${encodeURIComponent(item.owlClass)}">${displayValue}</a>`
                                };
                            }
                            
                            // Fallback for other formats
                            return {
                                label: item.label || item.value || JSON.stringify(item)
                            };
                        });
                    } else {
                        // Handle empty or unexpected response
                        this.dlResults = [{label: "No results found"}];
                    }
                    
                    this.isLoading = false;
                })
                .catch(error => {
                    console.error('Error processing LLM query:', error);
                    this.isLoading = false;
                    this.dlResults = [{label: 'Error: ' + error.message}];
                });
            });
        })
        .catch(error => {
            console.error('Error processing LLM query:', error);
            this.isLoading = false;
            
            // Display a more specific error message if it's from the NL params detection
            if (error.message && error.message.includes('query parser API')) {
                this.dlResults = [{label: 'Error detecting parameters: ' + error.message}];
            } else {
                this.dlResults = [{label: 'Error: ' + error.message}];
            }
            
            // Set detectedParams to show the error
            this.detectedParams = JSON.stringify({error: error.message}, null, 2);
        });
    },
  
  // Handle LLM query input change
  onLLMQueryChange(event) {
    this.llmQuery = event.target.value;
  },
  
  // Example queries for LLMQuery tab
  setSuperclassesCheesyPizzaExample(event) {
    if (event) event.preventDefault();
    this.llmQuery = "What are the superclasses of cheesy pizza?";
  },
  
  setSubclassesCheesyPizzaExample(event) {
    if (event) event.preventDefault();
    this.llmQuery = "What are the subclasses of cheesy pizza?";
  },

}));
});

