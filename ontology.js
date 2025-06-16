console.log('Loading ontology.js');

document.addEventListener('alpine:init', () => {
    // Create an Alpine.js store for sharing data between components
    Alpine.store('ontologyApp', {
	ontology: null,
	selectedClass: null,
	selectedProp: null,
	currentTab: 'Overview'
    });

    Alpine.data('ontologyApp', () => ({
	ontology: null,
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
      
	    // Listen for custom events from main.js
	    window.addEventListener('ontology:data', (event) => {
		if (event.detail && event.detail.ontology) {
		    this.ontology = event.detail.ontology;
		    this.processOntologyData();
		}
	    });
      
	    window.addEventListener('hash:changed', () => {
		this.checkUrlHash();
	    });

	    window.addEventListener('hashchange', () => {
		this.checkUrlHash();
	    });
  
	    // Initialize with default SPARQL query
	    this.setQueryClassesExampleQuery();
	    
	},
    
    // Process ontology data after loading
    processOntologyData() {
      if (!this.ontology) return;
      
      // Build the class map and ensure all classes are collapsed by default
      this.classesMap = new Map();
      this.ensureCollapsedState(this.ontology.classes);
      
      for (let i = 0; i < this.ontology.classes.length; i++) {
        this.classesMap.set(this.ontology.classes[i].owlClass, this.ontology.classes[i]);
      }
      
      // Build the properties map
      this.propsMap = new Map();
      for (let i = 0; i < this.ontology.properties.length; i++) {
        this.propsMap.set(this.ontology.properties[i].owlClass, this.ontology.properties[i]);
      }
      
      // Update the store
      Alpine.store('ontologyApp').ontology = this.ontology;
      
      // Check URL hash for navigation
      this.checkUrlHash();
      
      // Notify that ontology is loaded
      window.dispatchEvent(new CustomEvent('ontology:loaded'));
    },
    
    // Helper method to ensure all classes and their children are collapsed
    ensureCollapsedState(classes) {
      if (!classes) return;
      
      for (let i = 0; i < classes.length; i++) {
        const cls = classes[i];
        // Set all classes to collapsed (hidden)
        cls.collapsed = true;
        
        // Recursively collapse all children
        if (cls.children) {
          for (let j = 0; j < cls.children.length; j++) {
            if (cls.children[j]) {
              cls.children[j].collapsed = true;
              
              // Recursively collapse all deeper levels
              if (cls.children[j].children) {
                this.ensureCollapsedState(cls.children[j].children);
              }
            }
          }
        }
      }
    },
    
    fetchOntologyData() {
      this.isLoading = true;
      
      // Fetch the ontology data from the data folder
      fetch('/data/pizza.json')
        .then(response => {
          if (!response.ok) {
            throw new Error('Network response was not ok');
          }
          return response.json();
        })
        .then(data => {
          this.ontology = data;
          this.processOntologyData();
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
          }
          
          // Execute a DL query to get subclasses
          this.executeBrowseDLQuery(owlClass);
        } else {
          // In a real app, this would fetch from API
          this.isLoading = true;
          
          // Simulate API call
          setTimeout(() => {
            // For demo purposes, we'll just select the first class
            if (this.ontology.classes.length > 0) {
              this.selectedClass = this.ontology.classes[0];
              this.executeBrowseDLQuery(this.ontology.classes[0].owlClass);
            }
            this.isLoading = false;
          }, 500);
        }
      } else if (tab === 'DLQuery' && owlClass) {
        if (this.classesMap.has(owlClass)) {
          this.selectedClass = this.classesMap.get(owlClass);
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
    
    executeDLQuery(owlClass, queryType) {
      this.isLoading = true;
      
      // Make a real API call to the backend
      fetch(`/api/dlquery?query=${encodeURIComponent(owlClass)}&type=${queryType}&ontology=PIZZA`)
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
      const q = data.result.slice();
      let it = 0;
      
      while(it < q.length) {
        const cl = q[it];
        if ('children' in cl) {
          cl.collapsed = true;
          q.push(...cl.children);
        }
        this.classesMap.set(cl.owlClass, cl);
        it++;
      }
      
      this.ontology.classes = data.result;
      this.selectedClass = this.classesMap.get(owlClass);
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
        ['Homepage', `<a href="${submission.home_page}" target="_blank">${submission.home_page}</a>`],
        ['Documentation', `<a href="${submission.documentation}" target="_blank">${submission.documentation}</a>`],
        ['Publication', submission.publication],
        ['Ontology language', submission.has_ontology_language],
        ['License', 'CC-BY 4.0'],
        ['Authors', 'The Pizza Ontology Working Group'],
        ['Contact', '<a href="mailto:pizza@example.org">pizza@example.org</a>']
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
      // Update URL hash
      window.location.hash = `/DLQuery/${encodeURIComponent(owlClass)}/${queryType}`;
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
		  "VALUES ?class {OWL superclass <> <> { cheesypizza } } . } \n" +
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
	  console.log('Available methods:', Object.getOwnPropertyNames(this));
	  console.log('testMethod type:', typeof this.testMethod);
	  console.log('executeSparql type:', typeof this.executeSparql);

	  if (event) event.preventDefault();
	  this.isLoading = true;
  
	  const sparqlUrl = '/api/api/sparql.groovy';
	  const formData = new URLSearchParams();
	  formData.append('query', this.query.trim());
  
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
      return this.selectedClass && this.selectedClass.owlClass === node.owlClass;
    },
    
    // Toggle the collapsed state of a node
    toggleCollapsed(event, node) {
      event.preventDefault();
      event.stopPropagation();
      
      if (!node) return;
      
      // Toggle collapsed state
      node.collapsed = !node.collapsed;
      
      // If we're expanding a node, make sure all its children are collapsed
      if (!node.collapsed && node.children) {
        node.children.forEach(child => {
          if (child) child.collapsed = true;
        });
      }
    },
    
    handleNodeClick(event, owlClass) {
      event.preventDefault();
      
      const obj = this.classesMap.get(owlClass);
      if (!obj) return;
      
      // Update URL hash
      window.location.hash = `/Browse/${encodeURIComponent(owlClass)}`;
      
      // Set selected class
      this.selectedClass = obj;
      Alpine.store('ontologyApp').selectedClass = obj;
      
      this.currentTab = 'Browse';
      Alpine.store('ontologyApp').currentTab = 'Browse';
      
      // Expand the selected class to show its children
      if (obj.children && obj.children.length > 0) {
        obj.collapsed = false;
      }
      
      // Set DL query expression based on class label
      if (this.selectedClass) {
        let label = this.selectedClass.label.toLowerCase();
        this.dlQueryExp = label.includes(' ') ? `'${label}'` : label;
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
    },
    
    handlePropertyClick(event, owlClass) {
      event.preventDefault();
      
      const obj = this.propsMap.get(owlClass);
      if (!obj) return;
      
      // Update URL hash
      window.location.hash = `/Property/${encodeURIComponent(owlClass)}`;
      
      // Set selected property
      this.selectedProp = obj;
      Alpine.store('ontologyApp').selectedProp = obj;
      
      this.currentTab = 'Property';
      Alpine.store('ontologyApp').currentTab = 'Property';
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
    
    executeSearch(search) {
      // Direct Elasticsearch query for label matches
      fetch('/elastic/owl_class_index/_search', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          query: {
            bool: {
              must: [
                {match_bool_prefix: {label: search.toLowerCase()}},
              ]
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
      })
      .catch(error => {
        console.error('Elasticsearch error:', error);
        this.searchResults = [];
      });
    },

    handleSearchItemClick(search) {
      this.search = search;
      this.searchResultsShow = false;
    },

	detectNLParams() {
	    console.log('Detecting parameters from natural language query:', this.llmQuery);
	    
	    // Default values in case the API call fails
	    let query = "pizza";
	    let type = "superclass";
	    const direct = "false";
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
	            // Resolve with default values in case of error
	            resolve({
	                "query": query,
	                "type": type,
	                "direct": direct,
	                "labels": labels,
	                "axioms": axioms
	            });
	        });
	    });
	},
    // Process natural language query and convert to SPARQL
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
	            this.detectedParams = JSON.stringify(all_params, null, 2);
	            
	            // Build the query URL with parameters
	            const params = `query=${query}&type=${type}&direct=${direct}&labels=${labels}&axioms=${axioms}`;
	            const queryUrl = `${dlQueryUrl}?${params}`;
	   
	            // Execute the query with detected parameters
	            return fetch(queryUrl, {
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
	     });
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
	},
    
    // Handle LLM query input change
    onLLMQueryChange(event) {
      this.llmQuery = event.target.value;
    },
    
    // Example queries for LLMQuery tab
    setSuperclassesCheesyPizzaExample(event) {
      if (event) event.preventDefault();
      this.llmQuery = "What are the superclasses of cheesypizza?";
    },
    
    setSubclassesCheesyPizzaExample(event) {
      if (event) event.preventDefault();
      this.llmQuery = "What are the subclasses of cheesypizza?";
    },

  }));
});
