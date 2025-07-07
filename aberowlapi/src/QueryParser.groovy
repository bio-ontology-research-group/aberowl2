/* 
 * Copyright 2014 Luke Slater (lus11@aber.ac.uk).
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package src

//import org.semanticweb.owlapi.util.mansyntax.ManchesterOWLSyntaxClassExpressionParser;
//import org.semanticweb.owlapi.mansyntax.ManchesterOWLSyntaxEditorParser
import org.semanticweb.owlapi.manchestersyntax.parser.ManchesterOWLSyntaxClassExpressionParser
import org.semanticweb.owlapi.expression.OWLEntityChecker;
import org.semanticweb.owlapi.expression.ShortFormEntityChecker;
import org.semanticweb.owlapi.model.OWLClassExpression;
import org.semanticweb.owlapi.model.OWLDataFactory;
import org.semanticweb.owlapi.model.OWLOntology;
import org.semanticweb.owlapi.util.BidirectionalShortFormProvider;
import org.semanticweb.owlapi.model.*
import org.semanticweb.owlapi.util.BidirectionalShortFormProviderAdapter;

/**
 * Parses Manchester OWL Syntax strings into a normalised ontology class description.
 * 
 * @author Luke Slater
 */
public class QueryParser {
    private final BidirectionalShortFormProvider biSFormProvider;
    private final OWLOntology ontology;
    
    public QueryParser(ontology, sProvider) {
        this.ontology = ontology;
        biSFormProvider = new BidirectionalShortFormProviderAdapter(
            ontology.getOWLOntologyManager(),
            ontology.getImportsClosure(),
            sProvider
        );
    }
    
    /**
     * Convert a IRI string or a Manchester OWL Syntax query into a generalised class description.
     * 
     * @param mOwl String containing a class expression in Manchester OWL Syntax or IRI format
     * @return An OWLClassExpression generated from mOwl
     */
    public OWLClassExpression parse(String mOwl, boolean labels) {
 def result = null

 if (mOwl.startsWith("<") && mOwl.endsWith(">")) {
  mOwl = mOwl.substring(1, mOwl.length() - 1).trim();
         def iri = IRI.create(mOwl);
         result = this.ontology.getOWLOntologyManager().getOWLDataFactory().getOWLClass(iri);
        }else{
     try {
                // First try to find the class directly by name
                for (OWLClass cls : ontology.getClassesInSignature(true)) {
                    String shortForm = cls.getIRI().getFragment()
                    if (shortForm.equalsIgnoreCase(mOwl) ||
                        shortForm.replace("_", "").equalsIgnoreCase(mOwl.replace("_", ""))) {
                        return cls
                    }
                }
                
                // If not found directly, try with quotes for Manchester syntax
                // Check if the input contains spaces and is not already quoted
                if (mOwl.contains(" ") && !mOwl.startsWith("'") && !mOwl.endsWith("'")) {
                    // Add quotes around the entity name
                    mOwl = "'" + mOwl + "'";
                } else if (!mOwl.contains(" ") && !mOwl.startsWith("'") && !mOwl.endsWith("'")) {
                    // For single words, also try with quotes
                    mOwl = "'" + mOwl + "'";
                }
                
                OWLDataFactory dFactory = this.ontology.getOWLOntologyManager().getOWLDataFactory();
  def eChecker = new BasicEntityChecker(dFactory, ontology)
  def parser = new ManchesterOWLSyntaxClassExpressionParser(dFactory, eChecker);

  if(labels) {
      // Always use BasicEntityChecker to ensure consistent handling of underscores
      eChecker = new BasicEntityChecker(dFactory, ontology)
      parser = new ManchesterOWLSyntaxClassExpressionParser(dFactory, eChecker);
  }

                // Try to parse the input directly
                try {
                    result = parser.parse(mOwl);
                } catch(Exception firstTryException) {
                    // Always try with our enhanced BasicEntityChecker regardless of spaces
                    def basicChecker = new BasicEntityChecker(dFactory, ontology)
                    def basicParser = new ManchesterOWLSyntaxClassExpressionParser(dFactory, basicChecker);
                    
                    try {
                        result = basicParser.parse(mOwl);
                    } catch(Exception secondTryException) {
                        // If that fails too, rethrow the original exception
                        throw firstTryException;
                    }
                }
     } catch(Exception e) {
  throw new RuntimeException("QueryParser.groovy: Error parsing Manchester OWL Syntax query: " + mOwl+ " || " + e.getMessage())
         result = null
     }
	}

      return result
    }
}
